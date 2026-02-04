"""
Valence Backend Configuration - TypeDB 3.x Compatible
"""
from pydantic_settings import BaseSettings
from typing import List
import os


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""
    
    # TypeDB Cloud Configuration
    typedb_address: str = "https://localhost:1729"
    typedb_database: str = "valence"
    typedb_username: str = "admin"
    typedb_password: str = ""
    typedb_tls_enabled: bool = True
    
    # Anthropic API
    anthropic_api_key: str = ""
    claude_model: str = "claude-opus-4-20250514"
    
    # CORS - stored as string, parsed via property
    cors_origins_str: str = "http://localhost:5173"
    
    # File Storage
    upload_dir: str = "/app/uploads"
    
    # App Info
    app_name: str = "Valence Backend"
    app_version: str = "3.0.0"
    debug: bool = False
    
    @property
    def cors_origins_list(self) -> List[str]:
        """Parse CORS origins from comma-separated string."""
        return [origin.strip() for origin in self.cors_origins_str.split(",") if origin.strip()]
    
    @property
    def cors_origins(self) -> List[str]:
        """Alias for cors_origins_list."""
        return self.cors_origins_list
    
    @property
    def normalized_typedb_address(self) -> str:
        """
        Normalize TypeDB address for TypeDB 3.x.
        
        TypeDB 3.x Cloud requires https:// prefix for TLS connections.
        This property ensures the address has the correct format.
        """
        addr = self.typedb_address
        
        # TypeDB 3.x Cloud requires https:// prefix for TLS
        # Add https:// if no protocol specified
        if not addr.startswith("http://") and not addr.startswith("https://"):
            addr = f"https://{addr}"
        
        return addr
    
    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"


# Create settings instance
settings = Settings()


def get_settings() -> Settings:
    """Dependency injection for settings."""
    return settings
