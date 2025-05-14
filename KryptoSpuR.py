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
    """Schreibt den Benutzernamen in eine zentrale Liste,
    um die Gesamtzahl der User zu zählen. Die eigentlichen
    Transaktionsdaten liegen aber in separaten Dateien und
    bleiben privat."""
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
    """Berechnet steuerpflichtigen und steuerfreien Gewinn nach FIFO."""
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
        holding_days = (sell_row['date'] - buy['date']).days
        gain = lot_qty * (sell_row['price'] - buy['price'])
        if holding_days >= 365:
            exempt_gain += gain
        else:
            taxable_gain += gain
        buy['quantity'] -= lot_qty
        qty_to_sell -= lot_qty
        if buy['quantity'] > 0:
            remaining_buys.append(buy)

    updated_buys = pd.DataFrame(remaining_buys)
    return taxable_gain, exempt_gain, updated_buys


def estimated_tax(taxable_gain: float) -> float:
    """Schätzung nach Abgeltungsteuer (25 % + Soli)."""
    if taxable_gain <= 0:
        return 0.0
    abgeltung = taxable_gain * 0.25
    soli = abgeltung * 0.055  # 5,5 % Solidaritätszuschlag
    return abgeltung + soli  # ggf. Kirchensteuer separat

# -------------------------------------------------
# Streamlit-Oberfläche
# -------------------------------------------------

st.title('🇩🇪 KryptoSpuR – Dein FIFO Tracker')

username = st.text_input('Benutzername eingeben')
if username:
    user_count = register_user(username)
    st.caption(f'Es gibt aktuell {user_count} registrierte Nutzer:innen. (Der App-Eigentümer sieht **nur** diese Namen — nicht eure Daten)')

    df = load_user_data(username)

    # ---------- Formular für neue Transaktion ----------
    with st.form('transaction_form'):
        st.subheader('Transaktion erfassen')
        t_type = st.selectbox('Typ', ['Kauf', 'Verkauf'])
        coin = st.text_input('Coin-Symbol', value='BTC').upper()
        quantity = st.number_input('Menge', min_value=0.00000001, format='%.8f')
        price = st.number_input('Preis pro Coin (€)', min_value=0.0, format='%.2f')
        t_date = st.date_input('Datum', value=date.today())
        submitted = st.form_submit_button('Speichern')

    # ---------- Logik beim Speichern ----------
    if submitted:
        new_row = {
            'type': t_type,
            'coin': coin,
            'quantity': quantity,
            'price': price,
            'date': pd.to_datetime(t_date)
        }

        if t_type == 'Verkauf':
            buys = df[(df['coin'] == coin) & (df['type'] == 'Kauf')].copy()
            taxable_gain, exempt_gain, updated_buys = fifo_gain(buys, pd.Series(new_row))
            tax_due = estimated_tax(taxable_gain)
            st.success(f'Steuerpflichtiger Gewinn: {taxable_gain:.2f} €, geschätzte Steuer: {tax_due:.2f} €')
            df = df[~((df['coin'] == coin) & (df['type'] == 'Kauf'))]
            df = pd.concat([df, updated_buys], ignore_index=True)

        df = pd.concat([df, pd.DataFrame([new_row])], ignore_index=True)
        save_user_data(username, df)
       

    # ---------- Übersicht ----------
    st.subheader('Alle Transaktionen')
    st.dataframe(df)

    st.subheader('Jahresübersicht')
    year = st.number_input('Jahr wählen', min_value=2009, max_value=date.today().year, value=date.today().year)
    year_df = df[pd.to_datetime(df['date']).dt.year == year]
    if not year_df.empty:
        buys = year_df[year_df['type'] == 'Kauf'].copy()
        total_taxable = 0.0
        for _, sell in year_df[year_df['type'] == 'Verkauf'].iterrows():
            tg, _, _ = fifo_gain(buys[buys['coin'] == sell['coin']].copy(), sell)
            total_taxable += tg
        tax_est = estimated_tax(total_taxable)
        st.info(f'Steuerpflichtiger Gewinn {year}: {total_taxable:.2f} €, geschätzte Steuer: {tax_est:.2f} €')

    # ---------- PDF-Bericht ----------
    if st.button('PDF-Bericht erstellen'):
        pdf = FPDF()
        pdf.set_auto_page_break(auto=True, margin=15)
        pdf.add_page()
        pdf.set_font('Arial', size=12)
        pdf.cell(0, 10, 'Krypto FIFO Steuerbericht', ln=True, align='C')
        pdf.cell(0, 10, f'Benutzer: {username}', ln=True)
        pdf.cell(0, 10, f'Datum: {date.today().isoformat()}', ln=True)
        pdf.ln(4)
        for _, row in df.iterrows():
            line = f"{row['date'].date()} | {row['type']} | {row['coin']} | {row['quantity']} | {row['price']} €"
            pdf.cell(0, 8, line, ln=True)
        filepath = DATA_DIR / f'{username}_{date.today().isoformat()}.pdf'
        pdf.output(str(filepath))
        with open(filepath, 'rb') as f:
            st.download_button('PDF herunterladen', data=f, file_name=filepath.name, mime='application/pdf')

st.caption('Hinweis: Diese App ersetzt keine professionelle Steuerberatung.')
