"""
Streamlit application for PDF-based Retrieval-Augmented Generation (RAG) using Ollama + LangChain.

This application allows users to upload a PDF, process it,
and then ask questions about the content using a selected language model.
"""

import streamlit as st
import logging
import os
import tempfile
import shutil
import pdfplumber
import ollama
import time



from langchain_community.document_loaders import UnstructuredPDFLoader
from langchain_ollama import OllamaEmbeddings
from langchain_text_splitters import RecursiveCharacterTextSplitter
from langchain_community.vectorstores import Chroma
from langchain.prompts import ChatPromptTemplate, PromptTemplate
from langchain_core.output_parsers import StrOutputParser
from langchain_ollama.chat_models import ChatOllama
from langchain_core.runnables import RunnablePassthrough
from langchain.retrievers.multi_query import MultiQueryRetriever
from typing import List, Tuple, Dict, Any, Optional

# Set protobuf environment variable to avoid error messages
# This might cause some issues with latency but it's a tradeoff
os.environ["PROTOCOL_BUFFERS_PYTHON_IMPLEMENTATION"] = "python"

# Streamlit page configuration
st.set_page_config(
    page_title="Learn-Hub",
    page_icon="🎈",
    layout="wide",
    initial_sidebar_state="collapsed",
)

# Logging configuration
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

logger = logging.getLogger(__name__)


@st.cache_resource(show_spinner=True)
def extract_model_names(
    _models_info: Dict[str, Any],
) -> Tuple[str, ...]:
    """
    Extract model names from the provided models information.

    Args:
        _models_info (Dict[str, Any]): Serializable dictionary containing models information.

    Returns:
        Tuple[str, ...]: A tuple of model names.
    """
    logger.info("Extracting model names from models_info")
    model_names = tuple(model["model"] for model in _models_info["models"])
    logger.info(f"Extracted model names: {model_names}")
    return model_names


def create_vector_db(file_upload) -> Chroma:
    """
    Create a vector database from an uploaded PDF file.

    Args:
        file_upload (st.UploadedFile): Streamlit file upload object containing the PDF.

    Returns:
        Chroma: A vector store containing the processed document chunks.
    """
    logger.info(f"Creating vector DB from file upload: {file_upload.name}")
    temp_dir = tempfile.mkdtemp()

    path = os.path.join(temp_dir, file_upload.name)
    with open(path, "wb") as f:
        f.write(file_upload.getvalue())
        logger.info(f"File saved to temporary path: {path}")
        loader = UnstructuredPDFLoader(path)
        data = loader.load()

    text_splitter = RecursiveCharacterTextSplitter(chunk_size=7500, chunk_overlap=100)
    chunks = text_splitter.split_documents(data)
    logger.info("Document split into chunks")

    # Updated embeddings configuration
    embeddings = OllamaEmbeddings(model="nomic-embed-text")
    vector_db = Chroma.from_documents(
        documents=chunks,
        embedding=embeddings,
        collection_name="myRAG"
    )
    logger.info("Vector DB created")

    shutil.rmtree(temp_dir)
    logger.info(f"Temporary directory {temp_dir} removed")
    return vector_db


def process_question_stream(question: str, vector_db: Chroma, selected_model: str) -> str:
    """
    Process a user question using the vector database and selected language model.

    Args:
        question (str): The user's question.
        vector_db (Chroma): The vector database containing document embeddings.
        selected_model (str): The name of the selected language model.

    Returns:
        str: The generated response to the user's question.
    """



    logger.info(f"Processing question: {question} using model: {selected_model}")
    inicial_time = time.time()
    
    # Initialize LLM
    llm = ChatOllama(model=selected_model)
    
    # Query prompt template
    QUERY_PROMPT = PromptTemplate(
        input_variables=["question"],
        template="""You are an AI language model assistant. Your task is to generate 2
        different versions of the given user question to retrieve relevant documents from
        a vector database. By generating multiple perspectives on the user question, your
        goal is to help the user overcome some of the limitations of the distance-based
        similarity search. Provide these alternative questions separated by newlines.
        Original question: {question}""",
    )

    # Set up retriever
    retriever = MultiQueryRetriever.from_llm(
        vector_db.as_retriever(), 
        llm,
        prompt=QUERY_PROMPT
    )

    # RAG prompt template
    template = """Answer the question based ONLY on the following context:
    {context}
    Question: {question}
    """

    prompt = ChatPromptTemplate.from_template(template)

    # Create chain
    chain = (
        {"context": retriever, "question": RunnablePassthrough()}
        | prompt
        | llm
        | StrOutputParser()
    )

    # Stream response incrementally
    for partial_response in chain.stream(question):
        yield partial_response


@st.cache_data
def extract_all_pages_as_images(file_upload) -> List[Any]:
    """
    Extract all pages from a PDF file as images.

    Args:
        file_upload (st.UploadedFile): Streamlit file upload object containing the PDF.

    Returns:
        List[Any]: A list of image objects representing each page of the PDF.
    """
    logger.info(f"Extracting all pages as images from file: {file_upload.name}")
    pdf_pages = []
    with pdfplumber.open(file_upload) as pdf:
        pdf_pages = [page.to_image().original for page in pdf.pages]
    logger.info("PDF pages extracted as images")
    return pdf_pages


def delete_vector_db(vector_db: Optional[Chroma]) -> None:
    """
    Delete the vector database and clear related session state.

    Args:
        vector_db (Optional[Chroma]): The vector database to be deleted.
    """
    logger.info("Deleting vector DB")
    if vector_db is not None:
        vector_db.delete_collection()
        st.session_state.pop("pdf_pages", None)
        st.session_state.pop("file_upload", None)
        st.session_state.pop("vector_db", None)
        st.success("Collection and temporary files deleted successfully.")
        logger.info("Vector DB and related session state cleared")
        st.rerun()
    else:
        st.error("No vector database found to delete.")
        logger.warning("Attempted to delete vector DB, but none was found")


def main() -> None:
    """
    Main function to run the Streamlit application.
    """
    st.subheader("🧠 Faça perguntas ao PDF usando RAG + Ollama", divider="gray", anchor=False)

    # Get available models
    models_info = ollama.list()
    available_models = extract_model_names(models_info)

    # Create layout
    col1, col2 = st.columns([1.5, 2])

    # Initialize session state
    if "messages" not in st.session_state:
        st.session_state["messages"] = []
    if "vector_db" not in st.session_state:
        st.session_state["vector_db"] = None
    if "use_sample" not in st.session_state:
        st.session_state["use_sample"] = False

    # Model selection
    if available_models:
        selected_model = col2.selectbox(
            "Escolha um modelo disponível no seu sistema ↓", 
            available_models,
            key="model_select"
        )

    # Add checkbox for sample PDF
    use_sample = col1.toggle(
        "Use o PDF de exemplo (Artigo Atenttion Is All You Need)", 
        key="sample_checkbox"
    )
    
    # Clear vector DB if switching between sample and upload
    if use_sample != st.session_state.get("use_sample"):
        if st.session_state["vector_db"] is not None:
            st.session_state["vector_db"].delete_collection()
            st.session_state["vector_db"] = None
            st.session_state["pdf_pages"] = None
        st.session_state["use_sample"] = use_sample

    if use_sample:
        # Use the sample PDF
        sample_path = "attention.pdf"
        if os.path.exists(sample_path):
            if st.session_state["vector_db"] is None:
                with st.spinner("Processando PDF de exemplo..."):
                    loader = UnstructuredPDFLoader(file_path=sample_path)
                    data = loader.load()
                    text_splitter = RecursiveCharacterTextSplitter(chunk_size=7500, chunk_overlap=100)
                    chunks = text_splitter.split_documents(data)
                    st.session_state["vector_db"] = Chroma.from_documents(
                        documents=chunks,
                        embedding=OllamaEmbeddings(model="nomic-embed-text"),
                        collection_name="myRAG"
                    )
                    # Open and display the sample PDF
                    with pdfplumber.open(sample_path) as pdf:
                        pdf_pages = [page.to_image().original for page in pdf.pages]
                        st.session_state["pdf_pages"] = pdf_pages
        else:
            st.error("PDF de exemplo não encontrado no diretório atual.")
    else:
        # Regular file upload with unique key
        file_upload = col1.file_uploader(
            "Selecione um arquivo PDF ↓", 
            type="pdf", 
            accept_multiple_files=False,
            key="pdf_uploader"
        )

        if file_upload:
            if st.session_state["vector_db"] is None:
                with st.spinner("Processando upload do PDF..."):
                    st.session_state["vector_db"] = create_vector_db(file_upload)
                    pdf_pages = extract_all_pages_as_images(file_upload)
                    st.session_state["pdf_pages"] = pdf_pages

    # Display PDF if pages are available
    if "pdf_pages" in st.session_state and st.session_state["pdf_pages"]:
        # PDF display controls
        zoom_level = col1.slider(
            "Zoom Level", 
            min_value=100, 
            max_value=1000, 
            value=700, 
            step=50,
            key="zoom_slider"
        )

        # Display PDF pages
        with col1:
            with st.container(height=410, border=True):
                # Removed the key parameter from st.image()
                for page_image in st.session_state["pdf_pages"]:
                    st.image(page_image, width=zoom_level)

    # Delete collection button
    delete_collection = col1.button(
        "⚠️ Deletar collection", 
        type="secondary",
        key="delete_button"
    )

    if delete_collection:
        delete_vector_db(st.session_state["vector_db"])

    # Chat interface
    with col2:
        message_container = st.container(height=500, border=True)

        # Exibir histórico de mensagens
        for message in st.session_state["messages"]:
            avatar = "🤖" if message["role"] == "assistant" else "😎"
            with message_container.chat_message(message["role"], avatar=avatar):
                st.markdown(message["content"])

        # Entrada de texto para perguntas
        if prompt := st.chat_input("Enter a prompt here...", key="chat_input"):
            try:
                # Adicionar mensagem do usuário no histórico
                st.session_state["messages"].append({"role": "user", "content": prompt})
                with message_container.chat_message("user", avatar="😎"):
                    st.markdown(prompt)

                # Exibir resposta do assistente em tempo real
                with message_container.chat_message("assistente", avatar="🤖"):
                    placeholder = st.empty()  # Espaço reservado para a resposta
                    time_placeholder = st.empty()  # Espaço para o tempo total
                    with st.spinner(":gray[gerando resposta...]"):
                        if st.session_state["vector_db"] is not None:
                            response_text = ""  # Resposta completa
                            start_time = time.time()  # Tempo inicial
                            
                            # Streaming da resposta
                            for partial_response in process_question_stream(
                                prompt, st.session_state["vector_db"], selected_model
                            ):
                                response_text += partial_response
                                placeholder.markdown(response_text)
                            
                            end_time = time.time()  # Tempo final
                            time_spent = round(end_time - start_time, 2)

                            # Exibir o tempo total
                            time_placeholder.markdown(
                                f"<p style='font-size:12px; color:gray; margin-top:-10px;'>{time_spent}s</p>",
                                unsafe_allow_html=True
                            )
                        else:
                            st.warning("Por favor, faça o upload do PDF primeiro.")

                # Adicionar resposta do assistente no histórico
                if st.session_state["vector_db"] is not None:
                    st.session_state["messages"].append(
                        {"role": "assistant", "content": response_text}
                    )
            except Exception as e:
                st.error(f"Erro: {e}", icon="⛔️")
                logger.error(f"Erro ao processar: {e}")
if __name__ == "__main__":
    main()