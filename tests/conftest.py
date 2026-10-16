import os

# Set before test modules import the application and its cached settings.
os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///:memory:"
os.environ["AUTO_CREATE_SCHEMA"] = "true"
os.environ["INFERENCEIQ_LANGSMITH_ENABLED"] = "false"
os.environ["LANGGRAPH_CHECKPOINT_URL"] = ":memory:"
os.environ["INFERENCEIQ_AUTH_REQUIRED"] = "false"
os.environ["INFERENCEIQ_ADMIN_TOKEN"] = "test-admin-key"
os.environ["INFERENCEIQ_AUDIT_SIGNING_KEY"] = "test-audit-signing-key"
