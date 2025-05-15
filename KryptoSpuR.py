# KryptoSpuR.py
import os
import streamlit as st
import pandas as pd
from datetime import datetime, date
from pathlib import Path
from fpdf import FPDF
import openai
from sqlalchemy import create_engine, Column, Integer, String, Float, Date, MetaData, Table
from sqlalchemy.engine import Engine

# -------------------------------------------------
# Konfiguration & DB-Setup
# -------------------------------------------------
openai.api_key = os.getenv("OPENAI_API_KEY")  # ENV-Variable setzen
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///data/app.db")
engine: Engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
metadata = MetaData()

transactions = Table(
    'transactions', metadata,
    Column('id', Integer, primary_key=True, autoincrement=True),
    Column('username', String, nullable=False),
    Column('type', String, nullable=False),
    Column('coin', String, nullable=False),
    Column('quantity', Float, nullable=False),
    Column('price', Float, nullable=False),
    Column('date', Date, nullable=False)
)

users = Table(
    'users', metadata,
    Column('username', String, primary_key=True),
    Column('salary', Float, nullable=False),
    Column('tax_id', String, nullable=True)
)

metadata.create_all(engine)

# -------------------------------------------------
# Helferfunktionen (DB statt CSV)
# -------------------------------------------------
def register_user(username: str, salary: float, tax_id: str) -> int:
    conn = engine.connect()
    stmt = users.insert().values(username=username, salary=salary, tax_id=tax_id)
    stmt = stmt.on_conflict_do_update(index_elements=['username'], set_={'salary': salary, 'tax_id': tax_id})
    conn.execute(stmt)
    count = conn.execute(users.count()).scalar()
    conn.close()
    return count


def load_user_data(username: str) -> pd.DataFrame:
    query = transactions.select().where(transactions.c.username == username)
    df = pd.read_sql(query, engine, parse_dates=['date'])
    if df.empty:
        df = pd.DataFrame(columns=['type','coin','quantity','price','date'])
    return df


def save_user_data(username: str, df: pd.DataFrame):
    conn = engine.connect()
    conn.execute(transactions.delete().where(transactions.c.username == username))
    for _, row in df.iterrows():
        conn.execute(
            transactions.insert().values(
                username=username,
                type=row['type'],
                coin=row['coin'],
                quantity=float(row['quantity']),
                price=float(row['price']),
                date=row['date'].date()
            )
        )
    conn.close()

# -------------------------------------------------
# FIFO-Berechnung
# -------------------------------------------------
def fifo_gain(buys: pd.DataFrame, sell: pd.Series):
    qty = sell['quantity']; t_gain = 0.0; e_gain = 0.0; rem = []
    for _, b in buys.sort_values('date').iterrows():
        if qty <= 0:
            rem.append(b); continue
        lot = min(b['quantity'], qty)
        gain = lot * (sell['price'] - b['price'])
        days = (sell['date'] - b['date']).days
        if days >= 365:
            e_gain += gain
        else:
            t_gain += gain
        b['quantity'] -= lot; qty -= lot
        if b['quantity'] > 0:
            rem.append(b)
    return t_gain, e_gain, pd.DataFrame(rem)


def estimated_tax(gain: float, salary: float) -> float:
    if gain <= 0:
        return 0.0
    base = gain * 0.25
    soli = base * 0.055
    return base + soli

# -------------------------------------------------
# GPT-gestützter Steuerreport
# -------------------------------------------------
SYSTEM_PROMPT = """
Du bist ein deutschsprachiger Steuer-Assistent für Kryptowährungen. Generiere einen Finanzamt-tauglichen Steuerbericht im Markdown-Format anhand der übergebenen Transaktionen und Nutzerdaten.
"""
USER_TEMPLATE = """
Erstelle meinen Krypto-Steuerbericht.

Benutzername: {name}
Bruttojahresgehalt: {salary} €
Steuer-ID (optional): {tax_id}
Veranlagungsjahr: {year}

Transaktionen (JSON):
{tx_json}

Nutze Zeitzone Europe/Berlin, runde auf zwei Nachkommastellen.
"""

def markdown_report(df: pd.DataFrame, name: str, salary: float, tax_id: str, year: int) -> str:
    prompt = USER_TEMPLATE.format(
        name=name,
        salary=salary,
        tax_id=tax_id or 'n.v.',
        year=year,
        tx_json=df.to_json(orient='records', date_format='iso')
    )
    resp = openai.ChatCompletion.create(
        model='gpt-4o-mini',
        messages=[{'role':'system','content':SYSTEM_PROMPT}, {'role':'user','content':prompt}],
        temperature=0
    )
    return resp.choices[0].message.content


def pdf_from_markdown(md_text: str) -> bytes:
    pdf = FPDF()
    pdf.set_auto_page_break(True, margin=15)
    pdf.add_page()
    pdf.set_font('Arial', size=12)
    for line in md_text.splitlines():
        pdf.multi_cell(0, 8, line)
    return pdf.output(dest='S').encode('latin-1')

# -------------------------------------------------
# Streamlit UI
# -------------------------------------------------
st.set_page_config(page_title='KryptoSpuR', layout='wide')
st.title('🇩🇪 KryptoSpuR – Dein FIFO Tracker & Steuerreport')

username = st.text_input('Benutzername')
salary = st.number_input('Bruttojahresgehalt (€)', min_value=0.0, format='%.2f')
st.caption('Nur du siehst dein Gehalt; es dient nur zur Steuerberechnung.')
tax_id = st.text_input('Steuer-ID (optional)')
if username:
    user_count = register_user(username, salary, tax_id)
    st.caption(f'💼 Aktive Nutzer:innen: {user_count}')
    df = load_user_data(username)

    # Eingabe aktueller Coin-Preise
    coins = sorted(df['coin'].unique())
    prices = {}
    if coins:
        st.subheader('Aktuelle Coin-Preise')
        for c in coins:
            prices[c] = st.number_input(f'Preis {c} (€)', min_value=0.0, format='%.2f', key=f'pr_{c}')

    # Formular für neue Transaktion
    with st.form('new_tx'):
        st.subheader('Neue Transaktion')
        t_type = st.selectbox('Typ', ['Kauf', 'Verkauf'])
        coin = st.text_input('Coin-Symbol', 'BTC').upper()
        qty = st.number_input('Menge', min_value=0.00000001, format='%.8f')
        price = st.number_input('Preis (€)', min_value=0.0, format='%.2f')
        t_date = st.date_input('Datum', date.today())
        submitted = st.form_submit_button('Speichern')
    if submitted:
        new = {'type': t_type, 'coin': coin, 'quantity': qty, 'price': price, 'date': pd.to_datetime(t_date)}
        if t_type == 'Verkauf':
            t_gain, e_gain, updated = fifo_gain(df[(df.coin==coin)&(df.type=='Kauf')], pd.Series(new))
            st.success(f'Gewinn: {t_gain:.2f} €, steuerfrei: {e_gain:.2f} €, Steuer: {estimated_tax(t_gain, salary):.2f} €')
            df = df[~((df.coin==coin)&(df.type=='Kauf'))]
            df = pd.concat([df, updated], ignore_index=True)
        df = pd.concat([df, pd.DataFrame([new])], ignore_index=True)
        save_user_data(username, df)
        st.info('✅ Transaktion gespeichert')

    # Sortierung und Status-Berechnung
    df = df.sort_values('date').reset_index(drop=True)
    now = pd.to_datetime(datetime.now())
    df['anzeige'] = df.apply(lambda r: '🟢' if (r['type']=='Kauf' and (now-r['date'])>=pd.Timedelta(days=365, minutes=1))
                             else (f"{estimated_tax((prices.get(r['coin'],r['price'])-r['price'])*r['quantity'], salary):.2f} €" if r['type']=='Kauf' else ''), axis=1)

    # Editierbare Tabelle
    st.subheader('Alle Transaktionen (älteste zuerst)')
    edited = st.experimental_data_editor(df, num_rows='dynamic', use_container_width=True)
    if st.button('Änderungen speichern'):
        to_save = edited.drop(columns=['anzeige'])
        save_user_data(username, to_save)
        st.success('Daten aktualisiert')

    # Jahresübersicht
    year = st.number_input('Veranlagungsjahr', 2009, date.today().year, date.today().year)
    ydf = df[pd.to_datetime(df['date']).dt.year == year]
    if not ydf.empty:
        tg_sum = sum(fifo_gain(ydf[ydf.type=='Kauf'], pd.Series(r))[0] for _, r in ydf[ydf.type=='Verkauf'].iterrows())
        net_gain = tg_sum + sum(fifo_gain(ydf[ydf.type=='Kauf'], pd.Series(r))[1] for _, r in ydf[ydf.type=='Verkauf'].iterrows())
        adjusted = salary + net_gain
        st.info(f'{year}: Gewinn {tg_sum:.2f} €, steuerfrei {net_gain-tg_sum:.2f} €, neues Brutto: {adjusted:.2f} €')

    # PDF Steuerbericht via GPT
    if st.button('Finanzamt-PDF (GPT)'):
        md = markdown_report(df, username, salary, tax_id, date.today().year)
        pdf_data = pdf_from_markdown(md)
        st.download_button('PDF herunterladen', data=pdf_data,
                            file_name=f'{username}_{date.today().isoformat()}_Steuerreport.pdf',
                            mime='application/pdf')

st.caption('⚠️ Keine professionelle Steuerberatung.')
