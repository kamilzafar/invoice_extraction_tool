import streamlit as st
from dotenv import load_dotenv
import os
from PIL import Image
import google.generativeai as genai
import io

# Load environment variables
dotenv_path = os.path.join(os.getcwd(), '.env')
load_dotenv(dotenv_path)
# Configure Google Gemini
genai.configure(api_key=os.getenv('GOOGLE_API_KEY'))

model = genai.GenerativeModel('gemini-2.5-pro-preview-06-05')

def get_gemini_response(input_text, image=None):
    if image is None:
        response = model.generate_content(input_text)
    else:
        response = model.generate_content([input_text, image])
    return response.text

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
    input_prompt = "You are an expert in understanding invoices. Extract the following fields as a table with columns: Date, Invoice Number, Item, Amount, Total Amount, GST/ Sales tax, Vendor, Customer. Output only the table in CSV format."
    columns = ["Date", "Invoice Number", "Item", "Amount", "Total Amount", "GST/ Sales tax", "Vendor", "Customer"]
else:
    input_prompt = (
        "You are an expert in understanding bank statements (images or documents). "
        "Extract only the transactions as a table with columns: Date, Description, Debit, Credit, Balance. "
        "Output only the table in CSV format. "
        "For any transaction where the Description contains the word 'Deposit' (such as 'ABM Deposit', 'Direct Deposit', etc), "
        "the amount must always be placed in the Credit column, not Debit. "
        "If the extracted value for a Deposit is in the Debit column, move it to Credit and leave Debit empty. "
        "Make sure to get all the data from the document, including all transactions, and output it in CSV format."
    )
    columns = ["Date", "Description", "Debit", "Credit", "Balance"]

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
            data = []
            for line in lines:
                row = re.split(r'\t|\|\|,', line)
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
