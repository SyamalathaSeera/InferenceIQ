import os

from app.config import Settings

# Tests must never load local credentials or enable external tracing/providers.
Settings.model_config["env_file"] = None
os.environ["PROVIDER"] = "mock"
os.environ["OPENAI_API_KEY"] = ""
os.environ["ANTHROPIC_API_KEY"] = ""
os.environ["LANGSMITH_API_KEY"] = ""
os.environ["OLLAMA_ENABLED"] = "false"
os.environ["LANGSMITH_TRACING"] = "false"
os.environ["LANGCHAIN_TRACING_V2"] = "false"

# Set before test modules import the application and its cached settings.
os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///:memory:"
os.environ["AUTO_CREATE_SCHEMA"] = "true"
os.environ["INFERENCEIQ_LANGSMITH_ENABLED"] = "false"
os.environ["LANGGRAPH_CHECKPOINT_URL"] = ":memory:"
os.environ["INFERENCEIQ_AUTH_REQUIRED"] = "false"
os.environ["INFERENCEIQ_ADMIN_TOKEN"] = "test-admin-key"
os.environ["INFERENCEIQ_AUDIT_SIGNING_KEY"] = "test-audit-signing-key"
