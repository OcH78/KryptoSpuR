
# KryptoSpuR.py
import streamlit as st
import pandas as pd
from datetime import date
from pathlib import Path
from fpdf import FPDF

# -------------------------------------------------
# Daten-Ordner vorbereiten
# -------------------------------------------------
DATA_DIR = Path('data')
DATA_DIR.mkdir(exist_ok=True)
USERS_FILE = Path('users.txt')

# -------------------------------------------------
# Helferfunktionen
# -------------------------------------------------

def register_user(username: str) -> int:
    users = set()
    if USERS_FILE.exists():
        users = {u.strip() for u in USERS_FILE.read_text().splitlines() if u.strip()}
    if username and username not in users:
        users.add(username)
        USERS_FILE.write_text('\n'.join(sorted(users)))
    return len(users)


def load_user_data(username: str) -> pd.DataFrame:
    file = DATA_DIR / f'{username}_transactions.csv'
    if file.exists():
        return pd.read_csv(file, parse_dates=['date'])
    return pd.DataFrame(columns=['type', 'coin', 'quantity', 'price', 'date'])


def save_user_data(username: str, df: pd.DataFrame):
    file = DATA_DIR / f'{username}_transactions.csv'
    df.to_csv(file, index=False)


def fifo_gain(buys: pd.DataFrame, sell_row: pd.Series):
    qty_to_sell = sell_row['quantity']
    taxable_gain = 0.0
    exempt_gain = 0.0
    remaining_buys = []
    for _, buy in buys.sort_values('date').iterrows():
        if qty_to_sell <= 0:
            remaining_buys.append(buy)
            continue
        available = buy['quantity']
        if available <= 0:
            continue
        lot_qty = min(available, qty_to_sell)
        gain = lot_qty * (sell_row['price'] - buy['price'])
        holding_days = (sell_row['date'] - buy['date']).days
        if holding_days >= 365:
            exempt_gain += gain
        else:
            taxable_gain += gain
        buy['quantity'] -= lot_qty
        qty_to_sell -= lot_qty
        if buy['quantity'] > 0:
            remaining_buys.append(buy)
    return taxable_gain, exempt_gain, pd.DataFrame(remaining_buys)


def estimated_tax(taxable_gain: float) -> float:
    if taxable_gain <= 0:
        return 0.0
    abgelt = taxable_gain * 0.25
    soli = abgelt * 0.055
    return abgelt + soli

# -------------------------------------------------
# Streamlit-Oberfläche
# -------------------------------------------------

st.set_page_config(page_title="KryptoSpuR", layout="wide")
st.title('🇩🇪 KryptoSpuR – Dein FIFO Tracker')

username = st.text_input('Benutzername eingeben')
if username:
    count = register_user(username)
    st.caption(f'💼 Aktive Nutzer:innen: {count} (ich sehe nur Namen, keine Transaktionsdaten)')
    df = load_user_data(username)

    # ---------- Transaktion hinzufügen ----------
    with st.form('new_txn'):
        st.subheader('Neue Transaktion erfassen')
        t_type = st.selectbox('Typ', ['Kauf', 'Verkauf'])
        coin = st.text_input('Coin-Symbol', 'BTC').upper()
        qty = st.number_input('Menge', min_value=0.00000001, format='%.8f')
        price = st.number_input('Preis pro Coin (€)', min_value=0.0, format='%.2f')
        t_date = st.date_input('Datum', date.today())
        submit = st.form_submit_button('Speichern')
    if submit:
        new_row = {'type': t_type, 'coin': coin, 'quantity': qty, 'price': price, 'date': pd.to_datetime(t_date)}
        if t_type == 'Verkauf':
            buys = df[(df.coin == coin) & (df.type == 'Kauf')]
            tg, eg, updated = fifo_gain(buys, pd.Series(new_row))
            df = df[~((df.coin == coin) & (df.type == 'Kauf'))]
            df = pd.concat([df, updated], ignore_index=True)
            st.success(f'Steuerpflichtiger Gewinn: {tg:.2f} €, Steuer: {estimated_tax(tg):.2f} €')
        df = pd.concat([df, pd.DataFrame([new_row])], ignore_index=True)
        save_user_data(username, df)
        st.info('✅ Transaktion gespeichert!')

    # ---------- Transaktionen bearbeiten & löschen ----------
    st.subheader('Alle Transaktionen')
    edited = st.experimental_data_editor(df, num_rows='dynamic', use_container_width=True)
    if st.button('Änderungen speichern'):
        save_user_data(username, edited)
        st.success('Speicher erfolgreich. Änderungen angewendet!')

    # ---------- Jahresübersicht ----------
    st.subheader('Jahresübersicht')
    year = st.number_input('Jahr wählen', 2009, date.today().year, date.today().year)
    year_df = df[pd.to_datetime(df.date).dt.year == year]
    if not year_df.empty:
        total_tax = 0.0
        for _, sell in year_df[year_df.type == 'Verkauf'].iterrows():
            tg, _, _ = fifo_gain(year_df[year_df.type == 'Kauf'], sell)
            total_tax += tg
        st.info(f'{year}: Gewinn {total_tax:.2f} €, Steuer ~{estimated_tax(total_tax):.2f} €')

    # ---------- PDF-Bericht ----------
    if st.button('PDF-Bericht erstellen'):
        pdf = FPDF()
        pdf.set_auto_page_break(True, 15)
        pdf.add_page()
        pdf.set_font('Arial', size=12)
        pdf.cell(0, 10, 'KryptoSpuR – Steuerbericht', ln=True, align='C')
        pdf.ln(5)
        for _, row in edited.iterrows():
            line = f"{row.date.date()} | {row.type} | {row.coin} | {row.quantity} | {row.price} €"
            pdf.cell(0, 8, line, ln=True)
        out_path = DATA_DIR / f'{username}_{date.today().isoformat()}.pdf'
        pdf.output(str(out_path))
        with open(out_path, 'rb') as f:
            st.download_button('PDF herunterladen', data=f, file_name=out_path.name)

st.caption('Hinweis: Keine professionelle Steuerberatung.')
