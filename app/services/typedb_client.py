"""
TypeDB Cloud Client for Valence.

Handles connection to TypeDB Cloud with proper address normalization,
TLS handling, and transaction management.

IMPORTANT: TypeDB address must be host:port format, NO https:// prefix.
The driver handles TLS automatically when tls_enabled=True.
"""
import logging
from typing import Optional, Any, Generator
from contextlib import contextmanager

from typedb.driver import TypeDB, Credentials, TransactionType

from app.config import settings

logger = logging.getLogger(__name__)


class TypeDBClient:
    """
    TypeDB Cloud client wrapper.
    
    Handles:
    - Connection management with proper address format
    - TLS configuration
    - Transaction context managers
    - Schema operations
    """
    
    def __init__(self):
        self.address = settings.normalized_typedb_address
        self.database = settings.typedb_database
        self.credentials = Credentials(
            settings.typedb_username,
            settings.typedb_password
        )
        self.driver_options = DriverOptions(
            is_tls_enabled=settings.typedb_tls_enabled
        )
        
        self.driver: Optional[Any] = None
        self.is_connected = False
        self.connection_error: Optional[str] = None
        
        logger.info(f"TypeDB client initialized for {self.address}/{self.database}")
    
    def connect(self, raise_on_error: bool = False) -> bool:
        """Connect to TypeDB Cloud."""
        try:
            logger.info(f"Connecting to TypeDB at {self.address}...")
            
            self.driver = TypeDB.cloud_driver(
                self.address,
                Credentials(settings.typedb_username, settings.typedb_password)
            )
            
            # Verify database exists
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
    def transaction(self, tx_type: TransactionType = TransactionType.READ) -> Generator:
        """
        Context manager for TypeDB transactions.
        
        Usage:
            with client.transaction(TransactionType.WRITE) as tx:
                tx.query("insert ...")
                tx.commit()
        """
        if not self.is_connected:
            self.connect(raise_on_error=True)
        
        tx = self.driver.transaction(self.database, tx_type)
        try:
            yield tx
        finally:
            tx.close()
    
    @contextmanager
    def read_transaction(self) -> Generator:
        """Shortcut for read transaction."""
        with self.transaction(TransactionType.READ) as tx:
            yield tx
    
    @contextmanager
    def write_transaction(self) -> Generator:
        """Shortcut for write transaction (auto-commits on success)."""
        if not self.is_connected:
            self.connect(raise_on_error=True)
        
        tx = self.driver.transaction(self.database, TransactionType.WRITE)
        try:
            yield tx
            tx.commit()
        except Exception:
            # Transaction auto-closes on error
            raise
        finally:
            if tx.is_open():
                tx.close()
    
    @contextmanager
    def schema_transaction(self) -> Generator:
        """Transaction for schema operations."""
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
    
    def execute_query(self, query: str, tx_type: TransactionType = TransactionType.READ) -> list:
        """
        Execute a TypeQL query and return results.
        
        Args:
            query: TypeQL query string
            tx_type: Transaction type (READ, WRITE, SCHEMA)
            
        Returns:
            List of results
        """
        with self.transaction(tx_type) as tx:
            result = tx.query(query)
            if hasattr(result, 'resolve'):
                return list(result.resolve())
            return []
    
    def health_check(self) -> dict:
        """
        Check TypeDB connection health.
        
        Returns:
            Dict with connection status and details
        """
        if not self.is_connected:
            success = self.connect()
            if not success:
                return {
                    "connected": False,
                    "error": self.connection_error,
                    "address": self.address,
                    "database": self.database
                }
        
        try:
            # Try a simple query to verify connection is working
            with self.read_transaction() as tx:
                tx.query("match $x isa thing; limit 1;").resolve()
            
            return {
                "connected": True,
                "address": self.address,
                "database": self.database,
                "tls_enabled": settings.typedb_tls_enabled
            }
        except Exception as e:
            return {
                "connected": False,
                "error": str(e),
                "address": self.address,
                "database": self.database
            }


# Global client instance
typedb_client = TypeDBClient()


def get_typedb_client() -> TypeDBClient:
    """Dependency injection for TypeDB client."""
    return typedb_client
