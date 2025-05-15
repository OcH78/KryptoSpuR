# KryptoSpuR.py
import os
import streamlit as st
import pandas as pd
from datetime import datetime, date
from pathlib import Path
from fpdf import FPDF
import openai
import markdown2
import weasyprint

# -------------------------------------------------
# Konfiguration
# -------------------------------------------------
openai.api_key = os.getenv("OPENAI_API_KEY")  # ENV-Variable setzen

# -------------------------------------------------
# Daten-Ordner vorbereiten
# -------------------------------------------------
DATA_DIR = Path('data')
DATA_DIR.mkdir(exist_ok=True)
USERS_FILE = Path('users.txt')
SALARY_FILE = Path('salary.csv')

# -------------------------------------------------
# Helferfunktionen
# -------------------------------------------------

def register_user(username: str, salary: float, tax_id: str) -> int:
    users = {}
    if USERS_FILE.exists():
        for line in USERS_FILE.read_text().splitlines():
            user, sal, tid = line.split(',')
            users[user] = (float(sal), tid)
    users[username] = (salary, tax_id)
    # speichern Name, Gehalt, SteuerID
    USERS_FILE.write_text('\n'.join(f"{u},{users[u][0]},{users[u][1]}" for u in sorted(users)))
    SALARY_FILE.write_text(USERS_FILE.read_text())
    return len(users)


def load_user_data(username: str) -> pd.DataFrame:
    file = DATA_DIR / f"{username}_transactions.csv"
    if file.exists():
        return pd.read_csv(file, parse_dates=['date'])
    return pd.DataFrame(columns=['type','coin','quantity','price','date'])


def save_user_data(username: str, df: pd.DataFrame):
    file = DATA_DIR / f"{username}_transactions.csv"
    df.to_csv(file, index=False)

# -------------------------------------------------
# FIFO-Berechnung
# -------------------------------------------------

def fifo_gain(buys: pd.DataFrame, sell: pd.Series):
    qty = sell['quantity']; t_gain = 0.0; e_gain = 0.0; rem = []
    for _, b in buys.sort_values('date').iterrows():
        if qty <= 0:
            rem.append(b); continue
        lot = min(b['quantity'], qty)
        gain = lot*(sell['price']-b['price'])
        days = (sell['date']-b['date']).days
        if days >= 365:
            e_gain += gain
        else:
            t_gain += gain
        b['quantity'] -= lot; qty -= lot
        if b['quantity'] > 0:
            rem.append(b)
    return t_gain, e_gain, pd.DataFrame(rem)


def estimated_tax(gain: float, salary: float) -> float:
    if gain <= 0: return 0.0
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
        messages=[{'role':'system','content':SYSTEM_PROMPT},{'role':'user','content':prompt}],
        temperature=0
    )
    return resp.choices[0].message.content


def pdf_from_markdown(md_text: str) -> bytes:
    html = markdown2.markdown(md_text, extras=['tables'])
    return weasyprint.HTML(string=html).write_pdf()

# -------------------------------------------------
# Streamlit UI
# -------------------------------------------------
st.set_page_config(page_title='KryptoSpuR', layout='wide')
st.title('🇩🇪 KryptoSpuR – Dein FIFO Tracker & Steuerreport')

# Login
username = st.text_input('Benutzername')
salary = st.number_input('Bruttojahresgehalt (€)', min_value=0.0, format='%.2f')
st.caption('Nur du siehst dein Gehalt, es dient nur zur Steuerberechnung.')
tax_id = st.text_input('Steuer-ID (optional)')
if username:
    count = register_user(username, salary, tax_id)
    st.caption(f'💼 Nutzer: {count} (ich sehe nur Name, Gehalt & Steuer-ID)')
    df = load_user_data(username)

    # Eingabe aktueller Preise
    coins = sorted(df['coin'].unique())
    prices = {}
    if coins:
        st.subheader('Aktuelle Coin-Preise')
        for c in coins:
            prices[c] = st.number_input(f'Preis {c} (€)', min_value=0.0, format='%.2f', key=f'pr_{c}')

    # Neue Transaktion
    with st.form('txn'):
        t_type = st.selectbox('Typ', ['Kauf','Verkauf'])
        coin = st.text_input('Coin', 'BTC').upper()
        qty = st.number_input('Menge', min_value=0.00000001, format='%.8f')
        price = st.number_input('Preis (€)', min_value=0.0, format='%.2f')
        t_date = st.date_input('Datum', date.today())
        submit = st.form_submit_button('Speichern')
    if submit:
        new = {'type':t_type,'coin':coin,'quantity':qty,'price':price,'date':pd.to_datetime(t_date)}
        if t_type=='Verkauf':
            t_gain, e_gain, upd = fifo_gain(df[(df.coin==coin)&(df.type=='Kauf')], pd.Series(new))
            st.success(f'Gewinn: {t_gain:.2f} €, steuerfrei: {e_gain:.2f} €, Steuer: {estimated_tax(t_gain, salary):.2f} €')
            df = df[~((df.coin==coin)&(df.type=='Kauf'))]
            df = pd.concat([df, upd], ignore_index=True)
        df = pd.concat([df, pd.DataFrame([new])], ignore_index=True)
        save_user_data(username, df)
        st.info('✅ Transaktion gespeichert')

    # Sortierung und Anzeige
    df = df.sort_values('date').reset_index(drop=True)
    now = pd.to_datetime(datetime.now())
    df['anzeige'] = df.apply(lambda r: '🟢' if (r['type']=='Kauf' and (now-r['date'])>=pd.Timedelta(days=365, minutes=1)) 
                             else (f"{estimated_tax((prices.get(r['coin'],r['price'])-r['price'])*r['quantity'], salary):.2f} €" if r['type']=='Kauf' else ''), axis=1)
    st.subheader('Transaktionen (älteste zuerst)')
    edited = st.experimental_data_editor(df, num_rows='dynamic', use_container_width=True)
    if st.button('Speichern'):
        to_save = edited.drop(columns=['anzeige'])
        save_user_data(username, to_save)
        st.success('Daten aktualisiert')

    # Jahresübersicht & Gehaltsanpassung
    year = st.number_input('Jahr',2009,date.today().year,date.today().year)
    ydf = df[pd.to_datetime(df['date']).dt.year==year]
    if not ydf.empty:
        tg_sum = sum(fifo_gain(ydf[ydf.type=='Kauf'], pd.Series(r))[0] for _,r in ydf[ydf.type=='Verkauf'].iterrows())
        net_gain = tg_sum + sum(fifo_gain(ydf[ydf.type=='Kauf'], pd.Series(r))[1] for _,r in ydf[ydf.type=='Verkauf'].iterrows())
        adjusted_income = salary + net_gain
        st.info(f'{year}: Gewinn {tg_sum:.2f} €, Verluste/Vorträge steuerfrei: ' \
                f'{(net_gain-tg_sum):.2f} €, neues Brutto: {adjusted_income:.2f} €')

    # PDF Steuerbericht (GPT)
    if st.button('Finanzamt-PDF (GPT)'):
        md = markdown_report(df, username, salary, tax_id, date.today().year)
        pdf_data = pdf_from_markdown(md)
        st.download_button('PDF-Bericht herunterladen', data=pdf_data, file_name=f'{username}_{date.today().isoformat()}_Steuerreport.pdf', mime='application/pdf')

st.caption('⚠️ Keine professionelle Steuerberatung.')
