import streamlit as st
import imaplib
import email
from email.utils import parsedate_to_datetime, parseaddr
from email.header import decode_header
import re
import pandas as pd
from bs4 import BeautifulSoup
from datetime import datetime, timedelta
import time
from zoneinfo import ZoneInfo
import plotly.express as px
import os
import glob
import hashlib
import io


def partial_save_filename(email_addr, start_date, end_date, fetch_size_kb, char_limit):
    account_key = hashlib.sha256(email_addr.strip().lower().encode("utf-8")).hexdigest()[:12]
    return (
        f"partial_save_{account_key}_{start_date.strftime('%Y%m%d')}_to_"
        f"{end_date.strftime('%Y%m%d')}_{fetch_size_kb}kb_{char_limit}chars.csv"
    )

# --- Page Configuration ---
st.set_page_config(page_title="Gmail Extractor & Analytic", page_icon="📧", layout="centered")

def get_text_from_email(msg):
    """Safely extracts text content from complex multipart/HTML emails."""
    text = ""
    html = ""
    
    try:
        if msg.is_multipart():
            for part in msg.walk():
                content_type = part.get_content_type()
                if content_type == "text/plain":
                    payload = part.get_payload(decode=True)
                    if payload:
                        text += payload.decode(part.get_content_charset('utf-8') or 'utf-8', errors='ignore')
                elif content_type == "text/html":
                    payload = part.get_payload(decode=True)
                    if payload:
                        html += payload.decode(part.get_content_charset('utf-8') or 'utf-8', errors='ignore')
        else:
            content_type = msg.get_content_type()
            if content_type == "text/plain":
                payload = msg.get_payload(decode=True)
                if payload:
                    text = payload.decode(msg.get_content_charset('utf-8') or 'utf-8', errors='ignore')
            elif content_type == "text/html":
                payload = msg.get_payload(decode=True)
                if payload:
                    html = payload.decode(msg.get_content_charset('utf-8') or 'utf-8', errors='ignore')
    except Exception:
        pass # Gracefully handle any corruption from partial network downloads
    
    if html:
        try:
            soup = BeautifulSoup(html, "html.parser")
            return soup.get_text(separator=' ', strip=True)
        except Exception:
            pass
    return text.strip()

# --- UI Header ---
st.title("📧 Gmail Extractor & Analytic")
st.markdown("Connect securely to IMAP and extract emails to a Sheet-ready CSV.")

# --- App Password Reminder ---
st.info(
    "💡 **Need a Google App Password?**\n\n"
    "👉 [Click here to generate a Google App Password](https://myaccount.google.com/apppasswords) *(Opens in a new tab)*\n\n"
    "**Steps:** Log in ➡️ Create a new app name ➡️ Copy the 16-character code ➡️ Paste it below."
)

# --- Connection Drop Recovery ---
partial_files = glob.glob("partial_save_*.csv")
if partial_files:
    with st.expander("🚨 Recover Disconnected Files (Click to open)", expanded=True):
        st.warning(
            "**Did your phone screen turn off?**\n\n"
            "Mobile devices pause network connections when the screen sleeps, causing Streamlit to reset. "
            "Fortunately, the server saved your progress! Download your recovered files below:"
        )
        # Sort files by modification time (newest first)
        partial_files.sort(key=os.path.getmtime, reverse=True)
        for f_name in partial_files:
            try:
                with open(f_name, "rb") as file:
                    file_data = file.read()
                try:
                    recovered_df = pd.read_csv(io.BytesIO(file_data))
                    if "_IMAP UID" in recovered_df.columns:
                        recovered_df = recovered_df.drop(columns=["_IMAP UID"])
                        file_data = recovered_df.to_csv(index=False).encode("utf-8-sig")
                except Exception:
                    pass
                st.download_button(
                    label=f"📥 Download {f_name} ({os.path.getsize(f_name) // 1024} KB)",
                    data=file_data,
                    file_name=f_name,
                    mime="text/csv",
                    key=f"recover_{f_name}"
                )
            except Exception as e:
                st.error(f"Could not read {f_name}: {e}")

# --- Input Form ---
with st.form("extraction_form"):
    col1, col2 = st.columns(2)
    with col1:
        email_addr = st.text_input("Google Email Address", placeholder="you@gmail.com")
    with col2:
        app_pw = st.text_input("16-Digit App Password", type="password", placeholder="xxxx xxxx xxxx xxxx")
    
    col3, col4 = st.columns(2)
    with col3:
        start_date = st.date_input("Start Date", value=datetime(2026, 8, 1))
    with col4:
        end_date = st.date_input("End Date", value=datetime(2026, 8, 31))
        
    # Configurable Parameters for Network and Text size
    col5, col6 = st.columns(2)
    with col5:
        fetch_size_kb = st.number_input("Max Body Fetch Size (KB)", min_value=1, max_value=102400, value=10, step=1, 
                                        help="Limits how much of the body is downloaded. Headers are always downloaded fully.")
    with col6:
        char_limit = st.number_input("Text Character Limit", min_value=10, max_value=10000, value=500, step=100, 
                                     help="The maximum number of characters saved into the CSV per email.")

    # New Partial Save Configuration
    col7, col8 = st.columns(2)
    with col7:
        enable_partial_save = st.checkbox("Enable Partial Save (Local CSV)", value=True, 
                                          help="Periodically saves data to the local Codespace directory to prevent data loss in case of interruption.")
    with col8:
        save_interval = st.number_input("Save Interval (Emails)", min_value=100, max_value=5000, value=500, step=100, 
                                        help="How often to trigger the partial file save.")

    resume_partial = st.checkbox(
        "Resume from matching partial save",
        value=True,
        help="Continue after the last saved email when a checkpoint exists for this account and these settings."
    )

    submitted = st.form_submit_button("Start Optimized Extraction", use_container_width=True)

# --- Extraction Logic ---
if submitted:
    if not email_addr or not app_pw:
        st.error("⚠️ Please provide both your email and app password.")
    else:
        app_pw = app_pw.replace(" ", "")
        
        st.markdown("### 🖥️ Status Logs")
        log_container = st.empty()
        progress_bar = st.progress(0)
        
        try:
            log_container.info("Connecting to IMAP Server (imap.gmail.com)...")
            mail = imaplib.IMAP4_SSL("imap.gmail.com")
            
            try:
                mail.login(email_addr, app_pw)
                log_container.success("Authentication successful.")
            except Exception as e:
                raise Exception(f"Login failed. Check your App Password. Detail: {str(e)}")

            # Find 'All Mail' folder
            status, mailboxes = mail.list()
            all_mail_folder = "INBOX"
            for mailbox in mailboxes:
                if b'\\All' in mailbox:
                    mb_str = mailbox.decode('utf-8', errors='ignore')
                    all_mail_folder = mb_str.split(' "/" ')[-1] if ' "/" ' in mb_str else mb_str.split(' ')[-1]
                    break
            
            try:
                mail.select(all_mail_folder)
            except:
                mail.select("INBOX")

            # Parse dates for IMAP
            imap_start_date = start_date.strftime("%d-%b-%Y")
            imap_end_date = (end_date + timedelta(days=1)).strftime("%d-%b-%Y")

            partial_filename = partial_save_filename(
                email_addr, start_date, end_date, fetch_size_kb, char_limit
            )
            email_data = []
            last_saved_uid = 0
            if resume_partial and os.path.exists(partial_filename):
                try:
                    saved_df = pd.read_csv(partial_filename)
                    if "_IMAP UID" not in saved_df.columns:
                        st.warning("This partial save has no resume position. Starting a fresh download.")
                    else:
                        saved_uids = pd.to_numeric(saved_df["_IMAP UID"], errors="coerce").dropna()
                        if not saved_uids.empty:
                            last_saved_uid = int(saved_uids.max())
                            email_data = saved_df.to_dict("records")
                            log_container.info(f"Resuming after saved email UID {last_saved_uid}.")
                except Exception as e:
                    st.warning(f"Could not read the matching partial save; starting fresh. Detail: {e}")

            log_container.info(f"Searching mailbox from {imap_start_date} to {imap_end_date}...")
            
            status, data = mail.uid('search', None, 'SINCE', imap_start_date, 'BEFORE', imap_end_date)
            
            if not data[0] and not email_data:
                log_container.warning("No emails found in this date range.")
            else:
                matching_uids = data[0].split() if data[0] else []
                message_ids = [uid for uid in matching_uids if int(uid) > last_saved_uid]
                total_emails = len(message_ids)
                start_time = time.time()
                processed_count = 0
                last_save_count = 0 # Track partial saves
                
                batch_size = 100 
                local_tz = ZoneInfo("Asia/Hong_Kong")
                
                fetch_size_bytes = int(fetch_size_kb * 1024)
                
                # Fetches 100% of the Header, and dynamically limits ONLY the Body to X kilobytes
                fetch_command = f"(UID RFC822.SIZE BODY.PEEK[HEADER] BODY.PEEK[TEXT]<0.{fetch_size_bytes}>)"

                log_container.info(f"Found {total_emails} emails to download ({fetch_size_kb}KB limit per email body)...")

                for i in range(0, total_emails, batch_size):
                    batch_uids = message_ids[i:i+batch_size]
                    id_str = b",".join(batch_uids)
                    
                    status, msg_data = mail.uid('fetch', id_str, fetch_command)
                    
                    current_header = b""
                    current_body = b""
                    current_size_bytes = 0
                    current_uid = 0
                    current_email_date = "Unknown"
                    
                    for response_part in msg_data:
                        # Capture the header block and the body block
                        if isinstance(response_part, tuple):
                            descriptor = response_part[0].upper()
                            uid_match = re.search(rb'\bUID\s+(\d+)', descriptor)
                            if uid_match:
                                current_uid = int(uid_match.group(1))
                            size_match = re.search(rb'RFC822\.SIZE\s+(\d+)', descriptor)
                            if size_match:
                                current_size_bytes = int(size_match.group(1))
                            if b'HEADER' in descriptor:
                                current_header = response_part[1]
                            elif b'TEXT' in descriptor:
                                current_body = response_part[1]
                        
                        # A closing parenthesis byte signifies the end of a single email's data in the batch
                        elif response_part == b')':
                            if current_header or current_body:
                                # Reconstruct the raw email structure
                                raw_email = current_header + b"\r\n\r\n" + current_body
                                msg = email.message_from_bytes(raw_email)
                                
                                # Extract Subject
                                subject = msg.get("Subject", "")
                                if subject:
                                    decoded_list = decode_header(subject)
                                    subject_parts = []
                                    for decoded_string, charset in decoded_list:
                                        if isinstance(decoded_string, bytes):
                                            subject_parts.append(decoded_string.decode(charset or 'utf-8', errors='ignore'))
                                        else:
                                            subject_parts.append(decoded_string)
                                    subject = "".join(subject_parts)

                                # Extract Date
                                date_header = msg.get("Date")
                                date_str = ""
                                if date_header:
                                    try:
                                        dt = parsedate_to_datetime(date_header)
                                        dt_local = dt.astimezone(local_tz)
                                        date_str = dt_local.strftime("%Y-%m-%d %H:%M:%S")
                                    except:
                                        date_str = str(date_header)
                                current_email_date = date_str or "Unknown"
                                
                                # Extract Sender
                                sender_header = msg.get("From", "")
                                sender_email = parseaddr(sender_header)[1]
                                
                                # Extract Body
                                body_text = get_text_from_email(msg)
                                body_text = re.sub(r'\s+', ' ', body_text).strip()
                                body_text = body_text[:char_limit] 

                                email_data.append({
                                    "Date": date_str,
                                    "Sender Email": sender_email,
                                    "Email Title": subject,
                                    "Email Content": body_text,
                                    "Email Size (KB)": current_size_bytes / 1024,
                                    "_IMAP UID": current_uid
                                })
                                
                                # Reset for the next email in the batch
                                current_header = b""
                                current_body = b""
                                current_size_bytes = 0
                                current_uid = 0
                    
                    processed_count += len(batch_uids)
                    elapsed_time = time.time() - start_time
                    time_per_email = elapsed_time / processed_count
                    remaining_emails = total_emails - processed_count
                    est_remaining_sec = int(remaining_emails * time_per_email)
                    
                    progress_percentage = min(processed_count / total_emails, 1.0)
                    progress_bar.progress(progress_percentage)
                    
                    # --- NEW PARTIAL SAVE LOGIC ---
                    partial_msg = ""
                    if enable_partial_save and (processed_count - last_save_count) >= save_interval:
                        df_partial = pd.DataFrame(email_data)
                        df_partial.to_csv(partial_filename, index=False, encoding='utf-8-sig')
                        last_save_count = processed_count
                        partial_msg = f"\n\n*(💾 Safely backed up {processed_count} rows to `{partial_filename}` in Codespace)*"

                    log_container.info(
                        f"⚡ Processed {processed_count} / {total_emails} emails... "
                        f"(⏳ Est. remaining time: {est_remaining_sec} seconds | "
                        f"📅 Current email date: {current_email_date}){partial_msg}"
                    )

                mail.logout()

                period_str = f"{start_date.strftime('%Y-%m-%d')} to {end_date.strftime('%Y-%m-%d')}"
                filename = f"Gmail_summary ({period_str}).csv"

                df = pd.DataFrame(email_data).drop(columns=["_IMAP UID"], errors="ignore")
                csv = df.to_csv(index=False).encode('utf-8-sig')
                
                # Save Data and States for Download/Analytics Phase
                st.session_state['email_df'] = df
                st.session_state['download_csv'] = csv
                st.session_state['filename'] = filename
                st.session_state['download_ready'] = True
                st.session_state['show_analytics'] = False # Reset analytics view on new pull
                
                log_container.success(f"✅ Extraction complete in {int(time.time() - start_time)} seconds! File is ready.")

                if enable_partial_save:
                    try:
                        if os.path.exists(partial_filename):
                            os.remove(partial_filename)
                    except Exception:
                        pass

        except Exception as e:
            st.error(f"Error during extraction: {str(e)}")

# --- Download & Analytics Section ---
if st.session_state.get('download_ready', False):
    st.markdown("---")
    st.subheader("📥 Data Export & Analytics")
    
    col_dl, col_an = st.columns(2)
    with col_dl:
        st.download_button(
            label=f"Download {st.session_state['filename']}",
            data=st.session_state['download_csv'],
            file_name=st.session_state['filename'],
            mime='text/csv',
            use_container_width=True,
            type="primary"
        )
    with col_an:
        # Toggle analytics view on click
        if st.button("📊 Email analytic", use_container_width=True):
            st.session_state['show_analytics'] = not st.session_state['show_analytics']

    # --- Analytics Dashboard View ---
    if st.session_state.get('show_analytics', False):
        st.markdown("---")
        st.markdown("### 📈 Analytics Dashboard")
        
        df = st.session_state['email_df'].copy()
        
        if df.empty:
            st.info("No data available to analyze.")
        else:
            # 1. Clean Data for Analysis
            df['Date'] = pd.to_datetime(df['Date'], errors='coerce')
            df = df.dropna(subset=['Date', 'Sender Email']) # Remove blank rows
            
            # Create a YYYY-MM column for the histogram
            df['Month'] = df['Date'].dt.strftime('%Y-%m')
            
            if 'Email Size (KB)' not in df.columns:
                df['Email Size (KB)'] = 0

            # --- Table: Top Senders ---
            st.markdown("#### 🏆 Top Email Senders")
            top_sender_count = st.number_input(
                "Number of top email senders",
                min_value=1,
                max_value=1000,
                value=20,
                step=1,
                key="top_sender_count"
            )
            top_senders = (
                df.groupby('Sender Email', as_index=False)
                .agg(**{
                    'Total Emails': ('Sender Email', 'size'),
                    'Total Size (KB)': ('Email Size (KB)', 'sum')
                })
                .sort_values('Total Emails', ascending=False)
                .head(top_sender_count)
            )
            top_senders.insert(0, 'Top', range(1, len(top_senders) + 1))
            top_senders['Total Size (KB)'] = top_senders['Total Size (KB)'].apply(
                lambda size: f"{size:,.0f}"
            )

            st.dataframe(top_senders, use_container_width=True, hide_index=True)

            # --- Chart: Top Senders by Month ---
            st.markdown("#### 📊 Top Senders by Month")
            monthly_sender_count = st.number_input(
                "Number of top senders by month",
                min_value=1,
                max_value=1000,
                value=10,
                step=1,
                key="monthly_sender_count"
            )

            top_monthly_senders = df['Sender Email'].value_counts().head(monthly_sender_count).index
            df_top_monthly = df[df['Sender Email'].isin(top_monthly_senders)]
            
            monthly_counts = df_top_monthly.groupby(['Month', 'Sender Email']).size().reset_index(name='Count')
            
            if not monthly_counts.empty:
                fig = px.bar(
                    monthly_counts, 
                    x='Month', 
                    y='Count', 
                    color='Sender Email',
                    text='Count',
                    labels={'Count': 'Total Emails', 'Sender Email': 'Top Senders'}
                )
                
                fig.update_layout(
                    legend=dict(
                        orientation="h",       
                        yanchor="top",         
                        y=-0.2,                
                        xanchor="center",
                        x=0.5,
                        title=None             
                    ),
                    margin=dict(b=120)         
                )
                
                st.plotly_chart(fig, use_container_width=True)
            else:
                st.info("Not enough temporal data to generate a monthly histogram.")