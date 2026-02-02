"""
TypeDB Cloud Client for Valence - TypeDB 3.x API
"""
import logging
from typing import Optional, Any, Generator
from contextlib import contextmanager

from typedb.driver import TypeDB, Credentials, DriverOptions, TransactionType

from app.config import settings

logger = logging.getLogger(__name__)


class TypeDBClient:
    """TypeDB Cloud client wrapper."""
    
    def __init__(self):
        self.address = settings.normalized_typedb_address
        self.database = settings.typedb_database
        self.driver: Optional[Any] = None
        self.is_connected = False
        self.connection_error: Optional[str] = None
        
        logger.info(f"TypeDB client initialized for {self.address}/{self.database}")
    
    def connect(self, raise_on_error: bool = False) -> bool:
        """Connect to TypeDB Cloud."""
        try:
            logger.info(f"Connecting to TypeDB at {self.address}...")
            
            # TypeDB 3.x API: cloud_driver(address, Credentials, DriverOptions)
            self.driver = TypeDB.cloud_driver(
                self.address,
                Credentials(settings.typedb_username, settings.typedb_password),
                DriverOptions()
            )
            
            # Check if database exists
            if not self.driver.databases.contains(self.database):
                logger.info(f"Creating database: {self.database}")
                self.driver.databases.create(self.database)
            
            self.is_connected = True
            self.connection_error = None
            logger.info(f"Connected to TypeDB: {self.address}/{self.database}")
            return True
            
        except Exception as e:
            self.is_connected = False
            self.connection_error = f"Failed to connect to TypeDB: {e}"
            logger.error(self.connection_error)
            
            if raise_on_error:
                raise ConnectionError(self.connection_error)
            
            return False
    
    def close(self):
        """Close TypeDB connection."""
        if self.driver:
            self.driver.close()
            self.is_connected = False
            logger.info("TypeDB connection closed")
    
    @contextmanager
    def read_transaction(self) -> Generator:
        """Read transaction context manager."""
        if not self.is_connected:
            self.connect(raise_on_error=True)
        
        tx = self.driver.transaction(self.database, TransactionType.READ)
        try:
            yield tx
        finally:
            tx.close()
    
    @contextmanager
    def write_transaction(self) -> Generator:
        """Write transaction context manager."""
        if not self.is_connected:
            self.connect(raise_on_error=True)
        
        tx = self.driver.transaction(self.database, TransactionType.WRITE)
        try:
            yield tx
            tx.commit()
        except Exception:
            raise
        finally:
            if tx.is_open():
                tx.close()
    
    @contextmanager
    def schema_transaction(self) -> Generator:
        """Schema transaction context manager."""
        if not self.is_connected:
            self.connect(raise_on_error=True)
        
        tx = self.driver.transaction(self.database, TransactionType.SCHEMA)
        try:
            yield tx
            tx.commit()
        except Exception:
            raise
        finally:
            if tx.is_open():
                tx.close()
    
    def health_check(self) -> dict:
        """Check TypeDB connection health."""
        if not self.is_connected:
            success = self.connect()
            if not success:
                return {
                    "connected": False,
                    "error": self.connection_error,
                    "address": self.address,
                    "database": self.database
                }
        
        return {
            "connected": True,
            "address": self.address,
            "database": self.database
        }


# Global client instance
typedb_client = TypeDBClient()


def get_typedb_client() -> TypeDBClient:
    """Dependency injection for TypeDB client."""
    return typedb_client
