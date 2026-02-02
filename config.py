"""
Configuration settings loaded from environment variables.
"""
import os
from typing import List
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Application settings from environment variables."""
    
    # TypeDB Cloud - IMPORTANT: Address must be host:port, NO https://
    typedb_address: str = "localhost:1729"
    typedb_database: str = "valence"
    typedb_username: str = ""
    typedb_password: str = ""
    typedb_tls_enabled: bool = True
    
    # Claude API
    anthropic_api_key: str = ""
    claude_model: str = "claude-sonnet-4-20250514"
    
    # Server
    port: int = 8000
    uploads_dir: str = "/app/uploads"
    
    # CORS
    cors_origins: str = "http://localhost:5173"
    
    @property
    def cors_origins_list(self) -> List[str]:
        """Parse CORS origins from comma-separated string."""
        return [origin.strip() for origin in self.cors_origins.split(",")]
    
    @property
    def normalized_typedb_address(self) -> str:
        """
        Normalize TypeDB address to host:port format.
        Removes any protocol prefix (https://, http://) if present.
        """
        addr = self.typedb_address
        
        # Remove protocol prefix if present
        for prefix in ["https://", "http://"]:
            if addr.startswith(prefix):
                addr = addr[len(prefix):]
        
        # Remove trailing slashes or paths
        addr = addr.split("/")[0]
        
        return addr
    
    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"


# Global settings instance
settings = Settings()
