from pydantic_settings import BaseSettings
from pathlib import Path


class Settings(BaseSettings):
    database_url: str = "postgresql+asyncpg://insightflow:insightflow123@localhost:5432/insightflow"
    redis_url: str = "redis://localhost:6379/0"
    deepseek_api_key: str = ""
    deepseek_base_url: str = "https://api.deepseek.com"
    deepseek_model: str = "deepseek-chat"
    embedding_model: str = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
    embedding_dimensions: int = 384
    embedding_batch_size: int = 32
    embedding_cache_dir: str = str(Path.home() / ".cache" / "fastembed")
    chunk_size: int = 1000
    chunk_overlap: int = 200
    upload_dir: str = "./data/uploads"
    max_upload_size_mb: int = 25
    task_max_retries: int = 3
    task_retry_backoff_seconds: int = 5
    evaluation_output_dir: str = "./data/evaluations"
    cors_origins: str = "http://localhost:3000,http://127.0.0.1:3000"

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8", "extra": "ignore"}


settings = Settings()
