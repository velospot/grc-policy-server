from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts import ChatPromptTemplate
from langchain_ollama import ChatOllama, OllamaEmbeddings


class OllamaManager:
    def __init__(self, base_url: str, chat_model: str, embedding_model: str):
        """
        Initializes the Ollama wrappers for Chat and Embeddings.

        :param base_url: The URL where your Ollama instance is running (e.g., 'http://localhost:11434')
        :param chat_model: Name of the model for chat (e.g., 'llama3')
        :param embedding_model: Name of the model for embeddings (e.g., 'nomic-embed-text')
        """
        self.base_url = base_url
        self.chat_model_name = chat_model
        self.embedding_model_name = embedding_model

    def get_llm(self):
        """Returns the ChatOllama instance."""
        return ChatOllama(base_url=self.base_url, model=self.chat_model_name)

    def get_embedder(self):
        """Returns the OllamaEmbeddings instance."""
        return OllamaEmbeddings(
            base_url=self.base_url, model=self.embedding_model_name, temperature=0
        )

    def get_chain(self, system_prompt: str = "You are a helpful assistant."):
        """
        Returns a basic LangChain Expression Language (LCEL) chain.
        """
        llm = self.get_llm()
        system_msg = (
            "You are a document audit assistant. Your task is to compare two "
            "retrieved text chunks and summarize their relationship. "
            "Identify: 1) Shared information, 2) Contradictions, and 3) Unique details."
        )
        prompt = ChatPromptTemplate.from_messages(
            [("system", system_prompt), ("human", "{input}")]
        )

        # This creates a simple chain: Prompt -> LLM -> String Output
        return prompt | llm | StrOutputParser()


# # --- Example Usage ---
# if __name__ == "__main__":
#     # 1. Initialize the manager
#     ollama = OllamaManager(
#         base_url="http://localhost:11434",
#         chat_model="llama3",
#         embedding_model="nomic-embed-text",
#     )

#     # 2. Get the LLM and invoke directly
#     llm = ollama.get_llm()
#     # response = llm.invoke("Hello!")

#     # 3. Get the Embedder
#     embedder = ollama.get_embedder()
#     # vector = embedder.embed_query("Sample text for embedding")

#     # 4. Get a ready-to-use chain
#     chain = ollama.get_chain()
#     print(chain.invoke({"input": "Explain quantum physics in one sentence."}))
