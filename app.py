import streamlit as st
from PIL import Image
import google.generativeai as genai
import io
import auth_functions

# Configure Google Gemini
api_key = st.secrets["GOOGLE_API_KEY"]
genai.configure(api_key=api_key)

model = genai.GenerativeModel('gemini-2.5-pro-preview-06-05')

def get_gemini_response(input_text, image=None):
    if image is None:
        response = model.generate_content(input_text)
    else:
        response = model.generate_content([input_text, image])
    return response.text

# ------------------- AUTH -------------------
if 'user_info' not in st.session_state:
    col1, col2, col3 = st.columns([1,2,1])
    auth_form = col2.form(key='auth_form', clear_on_submit=False)
    email = auth_form.text_input('Email')
    password = auth_form.text_input('Password', type='password')
    auth_notification = col2.empty()
    if auth_form.form_submit_button('Sign In', use_container_width=True, type='primary'):
        with auth_notification, st.spinner('Signing in'):
            auth_functions.sign_in(email, password)
    if 'auth_success' in st.session_state:
        auth_notification.success(st.session_state.auth_success)
        del st.session_state.auth_success
    elif 'auth_warning' in st.session_state:
        auth_notification.warning(st.session_state.auth_warning)
        del st.session_state.auth_warning
    st.stop()
else:
    st.sidebar.write(f"Signed in as: {st.session_state.user_info.get('email', 'User')}")
    if st.sidebar.button('Sign Out', type='primary'):
        auth_functions.sign_out()
        st.rerun()

st.title('Invoice Extraction using Google Gemini')
st.write('Upload an invoice (image or PDF) and ask questions about the extracted content.')

doc_type = st.radio("Select document type", ["Vendor Bill", "Bank Statement"], horizontal=True)

upload_files = st.file_uploader('Upload one or more invoices (Image/PDF)', type=['jpg', 'jpeg', 'pdf', 'png'], accept_multiple_files=True)

images = []
pdf_files = []

if upload_files:
    for upload_file in upload_files:
        if upload_file.type in ['image/jpeg', 'image/jpg', 'image/png']:
            img = Image.open(upload_file)
            images.append((img, upload_file.name))
            st.image(img, caption=f'Uploaded Image: {upload_file.name}', use_container_width=True)
        elif upload_file.type == 'application/pdf':
            pdf_bytes = upload_file.read()
            pdf_files.append((pdf_bytes, upload_file.name))
            st.info(f'PDF uploaded: {upload_file.name}. The file will be sent to Gemini for extraction.')

if doc_type == "Vendor Bill":
    input_prompt = "You are an expert in understanding invoices. Extract the invoice as a table with columns: Date, Invoice Number, Item, Amount, Total Amount, GST/ Sales tax, Vendor, Customer. Output ONLY the table in CSV format, with the first row as the header. Do not include any explanations, summaries, or extra text."
    columns = ["Date", "Invoice Number", "Item", "Amount", "Total Amount", "GST/ Sales tax", "Vendor", "Customer"]
elif doc_type == "Bank Statement":
    input_prompt = "convert this bank statement to excel with col date, description, amount paid, amount received and balance"
    columns = ["Date", "Description", "Amount Paid", "Amount Received", "Balance"]

if st.button('Extract Information'):
    import pandas as pd
    import io
    import re
    all_dfs = []
    # Process images and PDFs together
    for file_info in images + pdf_files:
        if len(file_info) == 2 and isinstance(file_info[0], Image.Image):
            # Image
            img, fname = file_info
            response = get_gemini_response(input_prompt, img)
        else:
            # PDF
            pdf_bytes, fname = file_info
            pdf_part = {"mime_type": "application/pdf", "data": pdf_bytes}
            response = model.generate_content([pdf_part, input_prompt]).text
            print(f"Response from Gemini for {fname}: {response}")
        # Remove Markdown code block formatting if present
        response_clean = re.sub(r"^```csv\s*|^```\s*|```$", "", response, flags=re.MULTILINE).strip()
        df = None
        try:
            df = pd.read_csv(io.StringIO(response_clean))
            df = df[[col for col in columns if col in df.columns]]
        except Exception:
            lines = [line for line in response_clean.split('\n') if line.strip()]
            # --- Improved Markdown table extraction ---
            table_lines = []
            in_table = False
            for line in lines:
                if line.strip().startswith('|'):
                    in_table = True
                    table_lines.append(line)
                elif in_table:
                    break  # Stop at first non-table line after table started
            # Remove alignment row (---)
            table_lines = [l for l in table_lines if not set(l.replace('|','').strip()) <= set('-: ')]
            if len(table_lines) > 1:
                data = [[cell.strip() for cell in row.strip('|').split('|')] for row in table_lines]
                df = pd.DataFrame(data[1:], columns=[c.strip().replace(' ($)', '').replace('(', '').replace(')', '') for c in data[0]])
                # --- Ensure only the expected columns are kept, and rename if needed ---
                col_map = {c: c for c in columns}
                for c in df.columns:
                    c_norm = c.lower().replace(' ', '').replace('$','')
                    if c_norm == 'amountpaid':
                        col_map[c] = 'Amount Paid'
                    elif c_norm == 'amountreceived':
                        col_map[c] = 'Amount Received'
                    elif c_norm == 'date':
                        col_map[c] = 'Date'
                    elif c_norm == 'description':
                        col_map[c] = 'Description'
                    elif c_norm == 'balance':
                        col_map[c] = 'Balance'
                df = df.rename(columns=col_map)
                df = df[[col for col in columns if col in df.columns]]
            else:
                # Fallback: try manual split
                data = []
                for line in lines:
                    row = re.split(r'\t|,', line)
                    if len(row) == len(columns):
                        data.append([cell.strip() for cell in row])
                if data:
                    df = pd.DataFrame(data, columns=columns)
                else:
                    df = pd.DataFrame({"Gemini Output": [response_clean]})
        if df is not None and not df.empty:
            df = df[~(df.apply(lambda row: all(str(row[col]).strip().lower() == col.strip().lower() for col in df.columns), axis=1))]
            df['Filename'] = fname
            all_dfs.append(df)
    # Combine and show results
    if all_dfs:
        result = pd.concat(all_dfs, ignore_index=True)
        # Ensure all object columns are string type and replace 'nan' with empty string for Arrow compatibility
        for col in result.select_dtypes(include="object").columns:
            result[col] = result[col].astype(str).replace("nan", "")
        st.dataframe(result)
        excel_bytes = io.BytesIO()
        result.to_excel(excel_bytes, index=False)
        excel_bytes.seek(0)
        st.download_button("Download Extracted Excel", excel_bytes, "extracted_invoice.xlsx", mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    else:
        st.info("No structured data could be extracted from the uploaded files.")
