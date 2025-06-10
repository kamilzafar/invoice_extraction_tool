import streamlit as st
import pandas as pd
from google.cloud import documentai_v1 as documentai
from google.oauth2 import service_account
from google.cloud.documentai_toolbox import document as toolbox
import json
from PyPDF2 import PdfReader
import io

# ---- SETUP: Fill these with your actual processor IDs ----
INVOICE_PROCESSOR_ID = "5ef6485af5372708"  # e.g. 'xxx...'
BANK_PROCESSOR_ID    = "c231f3dddeaa44ca"     # e.g. 'yyy...'

# ---- Google Cloud Credentials ----
creds_json = json.loads(st.secrets["gcp_service_account"])
creds = service_account.Credentials.from_service_account_info(creds_json)
project_id = creds.project_id
location = "us"  # Or "eu", etc.

# ---- Helper: Call Document AI ----
def process_document(bytes_data, mime, proc_id, imageless=False):
    client = documentai.DocumentProcessorServiceClient(credentials=creds)
    name = client.processor_path(project_id, location, proc_id)
    raw = documentai.RawDocument(content=bytes_data, mime_type=mime)
    req = documentai.ProcessRequest(name=name, raw_document=raw, imageless_mode=imageless)
    res = client.process_document(request=req)
    return toolbox.Document.from_documentai_document(res.document)

# ---- Helper: Extract fields for Vendor Bills ----
def extract_vendor(wd: toolbox.Document) -> pd.DataFrame:
    inv = wd.search_entities("invoice_id")
    date = wd.search_entities("invoice_date")
    sup = wd.search_entities("supplier_name")
    cust = wd.search_entities("customer_name")
    total = wd.search_entities("total_amount")
    # Extract line items
    items = []
    for entity in wd.entities:
        if entity.type_ == "line_item":
            item = {
                "Item": "",
                "Amount": "",
                "GST/Sales Tax": ""
            }
            for prop in entity.properties:
                if prop.type_ == "item_description":
                    item["Item"] = prop.mention_text
                elif prop.type_ == "unit_price":
                    item["Amount"] = prop.mention_text
                elif prop.type_ == "tax_amount":
                    item["GST/Sales Tax"] = prop.mention_text
            items.append(item)
    df = pd.DataFrame(items)
    df["Date"] = date[0].mention_text if date else ""
    df["Invoice Number"] = inv[0].mention_text if inv else ""
    df["Total Amount"] = total[0].mention_text if total else ""
    df["Vendor"] = sup[0].mention_text if sup else ""
    df["Customer"] = cust[0].mention_text if cust else ""
    columns = ["Date", "Invoice Number", "Item", "Amount", "GST/Sales Tax", "Total Amount", "Vendor", "Customer"]
    for col in columns:
        if col not in df.columns:
            df[col] = ""
    return df[columns]

# ---- Helper: Extract fields for Bank Statements ----
def extract_bank(wd: toolbox.Document) -> pd.DataFrame:
    rows = []
    for page in wd.pages:
        for table in page.tables:
            headers = [cell.layout.text.strip().title() for cell in table.header_rows[0].cells]
            for row in table.body_rows:
                row_data = [cell.layout.text for cell in row.cells]
                row_dict = dict(zip(headers, row_data))
                rows.append(row_dict)
    df = pd.DataFrame(rows)
    cols = ["Date", "Description", "Debit", "Credit", "Balance"]
    for col in cols:
        if col not in df.columns:
            df[col] = ""
    return df[cols]

# ---- Streamlit UI ----
st.set_page_config(page_title="Batch AI Document Extractor", layout="wide")
st.title("📁 Batch Upload & AI Extraction")

files = st.file_uploader(
    "Upload one or more documents (PDF, PNG, JPG, JPEG)", 
    type=["pdf", "png", "jpg", "jpeg"], 
    accept_multiple_files=True
)

doc_type = st.radio("Select document type", ["Vendor Bill", "Bank Statement"], horizontal=True)

if files:
    proc_id = INVOICE_PROCESSOR_ID if doc_type == "Vendor Bill" else BANK_PROCESSOR_ID
    if st.button("Start Extraction", type="primary"):
        all_dfs = []
        with st.spinner("Processing documents..."):
            for f in files:
                try:
                    # -- Detect number of pages for PDFs
                    imageless = False
                    num_pages = None
                    if f.type == "application/pdf":
                        pdf_bytes = f.read()
                        pdf_stream = io.BytesIO(pdf_bytes)
                        reader = PdfReader(pdf_stream)
                        num_pages = len(reader.pages)
                        f.seek(0)  # Reset file pointer for next read
                        if num_pages > 30:
                            st.error(f"❌ {f.name} has {num_pages} pages. Google Document AI sync mode only supports up to 30 pages. Please split the file.")
                            continue
                        elif num_pages > 15:
                            imageless = True
                            st.warning(f"⚠️ {f.name} has {num_pages} pages. Using imageless mode (up to 30 pages per doc).")
                        wrapped = process_document(pdf_bytes, f.type, proc_id, imageless=imageless)
                    else:
                        wrapped = process_document(f.read(), f.type, proc_id, imageless=False)
                    df = extract_vendor(wrapped) if doc_type == "Vendor Bill" else extract_bank(wrapped)
                    df["Filename"] = f.name
                    all_dfs.append(df)
                except Exception as e:
                    st.error(f"⚠️ Error processing {f.name}: {e}")
        if all_dfs:
            result = pd.concat(all_dfs, ignore_index=True)
            st.success(f"Processed {len(all_dfs)} files — {len(result)} rows extracted.")
            st.dataframe(result)
            # Write Excel to bytes for download
            excel_bytes = io.BytesIO()
            result.to_excel(excel_bytes, index=False)
            excel_bytes.seek(0)
            st.download_button("📥 Download All Results as Excel", excel_bytes, "extracted_data.xlsx", mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
