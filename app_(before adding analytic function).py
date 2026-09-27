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

# --- Page Configuration ---
st.set_page_config(page_title="Email Extractor", page_icon="📧", layout="centered")

def get_text_from_email(msg):
    """Safely extracts text content from complex multipart/HTML emails."""
    text = ""
    html = ""
    
    if msg.is_multipart():
        for part in msg.walk():
            content_type = part.get_content_type()
            if content_type == "text/plain":
                text += part.get_payload(decode=True).decode(part.get_content_charset('utf-8') or 'utf-8', errors='ignore')
            elif content_type == "text/html":
                html += part.get_payload(decode=True).decode(part.get_content_charset('utf-8') or 'utf-8', errors='ignore')
    else:
        content_type = msg.get_content_type()
        if content_type == "text/plain":
            text = msg.get_payload(decode=True).decode(msg.get_content_charset('utf-8') or 'utf-8', errors='ignore')
        elif content_type == "text/html":
            html = msg.get_payload(decode=True).decode(msg.get_content_charset('utf-8') or 'utf-8', errors='ignore')
    
    if html:
        soup = BeautifulSoup(html, "html.parser")
        return soup.get_text(separator=' ', strip=True)
    return text.strip()

# --- UI Header ---
st.title("📧 Email Extraction Task")
st.markdown("Connect securely to IMAP and extract emails to a Sheet-ready CSV.")

# --- App Password Reminder ---
st.info(
    "💡 **Need a Google App Password?**\n\n"
    "👉 [Click here to generate a Google App Password](https://myaccount.google.com/apppasswords) *(Opens in a new tab)*\n\n"
    "**Steps:** Log in ➡️ Create a new app name ➡️ Copy the 16-character code ➡️ Paste it below."
)

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
        
    # --- NEW: Configurable Parameters for Network and Text size ---
    col5, col6 = st.columns(2)
    with col5:
        fetch_size_kb = st.number_input("Max Fetch Size per Email (KB)", min_value=1, max_value=102400, value=10, step=1, 
                                        help="Limits how much of each email is downloaded over the network. Smaller = Faster.")
    with col6:
        char_limit = st.number_input("Text Character Limit", min_value=10, max_value=10000, value=500, step=100, 
                                     help="The maximum number of characters saved into the CSV per email.")

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

            log_container.info(f"Searching mailbox from {imap_start_date} to {imap_end_date}...")
            
            status, data = mail.search(None, 'SINCE', imap_start_date, 'BEFORE', imap_end_date)
            
            if not data[0]:
                log_container.warning("No emails found in this date range.")
            else:
                message_ids = data[0].split()
                total_emails = len(message_ids)
                
                email_data = []
                start_time = time.time()
                processed_count = 0
                
                # Setup metrics for time estimation
                batch_size = 100 
                local_tz = ZoneInfo("Asia/Hong_Kong")
                
                # --- DYNAMIC FETCH STRING CREATION ---
                fetch_size_bytes = int(fetch_size_kb * 1024)
                fetch_command = f"(BODY.PEEK[]<0.{fetch_size_bytes}>)"

                log_container.info(f"Found {total_emails} emails. Starting partial download ({fetch_size_kb}KB limit per email)...")

                for i in range(0, total_emails, batch_size):
                    batch_ids = message_ids[i:i+batch_size]
                    id_str = b",".join(batch_ids)
                    
                    # Use the dynamically generated fetch command
                    status, msg_data = mail.fetch(id_str, fetch_command)
                    
                    for response_part in msg_data:
                        if isinstance(response_part, tuple):
                            msg = email.message_from_bytes(response_part[1])
                            
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
                            
                            # Extract Sender
                            sender_header = msg.get("From", "")
                            sender_email = parseaddr(sender_header)[1]
                            
                            # Extract Body
                            body_text = get_text_from_email(msg)
                            body_text = re.sub(r'\s+', ' ', body_text).strip()
                            
                            # Apply the dynamic character limit from UI
                            body_text = body_text[:char_limit] 

                            email_data.append({
                                "Date": date_str,
                                "Sender Email": sender_email,
                                "Email Title": subject,
                                "Email Content": body_text
                            })
                    
                    processed_count += len(batch_ids)
                    elapsed_time = time.time() - start_time
                    time_per_email = elapsed_time / processed_count
                    remaining_emails = total_emails - processed_count
                    est_remaining_sec = int(remaining_emails * time_per_email)
                    
                    progress_percentage = min(processed_count / total_emails, 1.0)
                    progress_bar.progress(progress_percentage)
                    
                    log_container.info(
                        f"⚡ Processed {processed_count} / {total_emails} emails... "
                        f"(⏳ Est. remaining time: {est_remaining_sec} seconds)"
                    )

                mail.logout()

                period_str = f"{start_date.strftime('%Y-%m-%d')} to {end_date.strftime('%Y-%m-%d')}"
                filename = f"Gmail_summary ({period_str}).csv"

                df = pd.DataFrame(email_data)
                csv = df.to_csv(index=False).encode('utf-8-sig')
                
                st.session_state['download_csv'] = csv
                st.session_state['filename'] = filename
                st.session_state['download_ready'] = True
                
                log_container.success(f"✅ Extraction complete in {int(time.time() - start_time)} seconds! File is ready.")

        except Exception as e:
            st.error(f"Error during extraction: {str(e)}")

# --- Download Section ---
if st.session_state.get('download_ready', False):
    st.markdown("---")
    st.subheader("📥 Download Your Data")
    st.download_button(
        label=f"Download {st.session_state['filename']}",
        data=st.session_state['download_csv'],
        file_name=st.session_state['filename'],
        mime='text/csv',
        use_container_width=True,
        type="primary"
    )