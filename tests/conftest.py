import os

# Tests never send traces to LangSmith, even when .env turns tracing on (load_dotenv does not override this).
os.environ["LANGSMITH_TRACING"] = "false"
os.environ["LANGCHAIN_TRACING_V2"] = "false"
os.environ["RESOLVER_AGENT_TRACING"] = "false"
