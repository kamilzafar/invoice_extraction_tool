# app.py
import streamlit as st
import os
import tempfile
from google.cloud import documentai_v1 as documentai
from google.oauth2 import service_account
import pandas as pd

# ---- Setup Google Credentials ----
creds_json = st.secrets["gcp_service_account"]
creds = service_account.Credentials.from_service_account_info(creds_json)

project_id = creds.project_id
location = "us"  # or use "eu" if your processor is in the EU
vendor_processor_id = "YOUR_VENDOR_PROCESSOR_ID"  # Replace with your Invoice parser processor ID
form_processor_id = "YOUR_FORM_PROCESSOR_ID"  # Replace with your general parser ID for bank statements

# ---- Helpers ----
def process_document(file_bytes, mime_type, processor_id):
    client = documentai.DocumentUnderstandingServiceClient(credentials=creds)
    name = f"projects/{project_id}/locations/{location}/processors/{processor_id}"
    raw_document = documentai.RawDocument(content=file_bytes, mime_type=mime_type)
    request = documentai.ProcessRequest(name=name, raw_document=raw_document)
    result = client.process_document(request=request)
    return result.document


def extract_vendor_invoice_data(document):
    data = {
        "Date": "",
        "Invoice Number": "",
        "Item": [],
        "Amount": [],
        "Total Amount": "",
        "GST/Sales Tax": [],
        "Vendor": "",
        "Customer": ""
    }

    for entity in document.entities:
        key = entity.type_.lower()
        val = entity.mention_text

        if "invoice_id" in key:
            data["Invoice Number"] = val
        elif "invoice_date" in key:
            data["Date"] = val
        elif "supplier" in key:
            data["Vendor"] = val
        elif "customer" in key:
            data["Customer"] = val
        elif "total_amount" in key:
            data["Total Amount"] = val
        elif "line_item" in key and entity.properties:
            item, amount, gst = "", "", ""
            for prop in entity.properties:
                if "description" in prop.type_.lower():
                    item = prop.mention_text
                elif "unit_price" in prop.type_.lower():
                    amount = prop.mention_text
                elif "tax_amount" in prop.type_.lower():
                    gst = prop.mention_text
            data["Item"].append(item)
            data["Amount"].append(amount)
            data["GST/Sales Tax"].append(gst)

    return pd.DataFrame({
        "Date": [data["Date"]] * len(data["Item"]),
        "Invoice Number": [data["Invoice Number"]] * len(data["Item"]),
        "Item": data["Item"],
        "Amount": data["Amount"],
        "Total Amount": [data["Total Amount"]] * len(data["Item"]),
        "GST/Sales Tax": data["GST/Sales Tax"],
        "Vendor": [data["Vendor"]] * len(data["Item"]),
        "Customer": [data["Customer"]] * len(data["Item"])
    })


def extract_bank_statement_data(document):
    rows = []
    for page in document.pages:
        for table in page.tables:
            headers = [cell.layout.text.lower() for cell in table.header_rows[0].cells]
            for row in table.body_rows:
                row_data = [cell.layout.text for cell in row.cells]
                row_dict = dict(zip(headers, row_data))
                rows.append(row_dict)

    df = pd.DataFrame(rows)
    df = df.rename(columns=lambda x: x.strip().title())
    keep_cols = ["Date", "Description", "Debit", "Credit", "Balance"]
    df = df[[col for col in keep_cols if col in df.columns]]
    return df


# ---- Streamlit UI ----
st.set_page_config(page_title="Document Type Extractor", layout="centered")
st.title("📑 AI-Powered Document Extractor")

uploaded_files = st.file_uploader("Upload one or more documents (PDF/Image)", type=["pdf", "png", "jpg", "jpeg"], accept_multiple_files=True)

doc_type = st.radio("What type of document are these?", ["Vendor Bill", "Bank Statement"])

if uploaded_files:
    processor_id = vendor_processor_id if doc_type == "Vendor Bill" else form_processor_id
    all_data = []

    with st.spinner("Processing all documents with Google Document AI..."):
        for uploaded_file in uploaded_files:
            mime = uploaded_file.type
            file_bytes = uploaded_file.read()
            try:
                doc = process_document(file_bytes, mime, processor_id)
                if doc_type == "Vendor Bill":
                    df = extract_vendor_invoice_data(doc)
                else:
                    df = extract_bank_statement_data(doc)
                all_data.append(df)
            except Exception as e:
                st.warning(f"Failed to process {uploaded_file.name}: {e}")

    if all_data:
        final_df = pd.concat(all_data, ignore_index=True)
        st.success("✅ All files processed successfully!")
        st.dataframe(final_df)

        st.download_button("📥 Download Excel File", final_df.to_excel(index=False), file_name="extracted_batch.xlsx")