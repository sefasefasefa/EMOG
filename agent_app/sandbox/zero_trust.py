"""
Zero-Trust Security Module
Provides Vault integration for secrets management and mTLS for service-to-service communication.
"""
import os
import json
import ssl
import base64
import hashlib
import hmac
import time
import threading
import subprocess
from typing import Dict, Any, Optional, List, Callable
from dataclasses import dataclass, field, asdict
from datetime import datetime, timedelta
from enum import Enum
from pathlib import Path
from functools import wraps
from contextlib import contextmanager
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa, padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from cryptography import x509
from cryptography.x509.oid import NameOID


class SecretType(Enum):
    """Types of secrets managed by the vault"""
    API_KEY = "api_key"
    DATABASE_PASSWORD = "database_password"
    JWT_SECRET = "jwt_secret"
    ENCRYPTION_KEY = "encryption_key"
    OAUTH_TOKEN = "oauth_token"
    SSH_KEY = "ssh_key"
    CERTIFICATE = "certificate"
    GENERIC = "generic"


class VaultBackend(Enum):
    """Supported vault backends"""
    HASHICORP_VAULT = "hashicorp_vault"
    AWS_SECRETS_MANAGER = "aws_secrets_manager"
    AZURE_KEY_VAULT = "azure_key_vault"
    GOOGLE_SECRET_MANAGER = "google_secret_manager"
    LOCAL_ENCRYPTED = "local_encrypted"


@dataclass
class Secret:
    """A secret stored in the vault"""
    id: str
    name: str
    secret_type: SecretType
    value: str
    version: int = 1
    created_at: str = field(default_factory=lambda: datetime.utcnow().isoformat())
    updated_at: str = field(default_factory=lambda: datetime.utcnow().isoformat())
    expires_at: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)
    tags: List[str] = field(default_factory=list)


@dataclass
class Certificate:
    """X.509 Certificate for mTLS"""
    id: str
    common_name: str
    sans: List[str] = field(default_factory=list)  # Subject Alternative Names
    not_before: str = field(default_factory=lambda: datetime.utcnow().isoformat())
    not_after: str = field(default_factory=lambda: (datetime.utcnow() + timedelta(days=365)).isoformat())
    cert_pem: str = ""
    key_pem: str = ""
    ca_pem: str = ""
    serial_number: str = ""
    fingerprint: str = ""


class VaultClient:
    """
    Abstract vault client supporting multiple backends.
    Prevents hardcoded secrets by providing centralized secret management.
    """
    
    def __init__(self, backend: VaultBackend = VaultBackend.LOCAL_ENCRYPTED, config: Dict = None):
        self.backend = backend
        self.config = config or {}
        self._lock = threading.Lock()
        self._cache: Dict[str, Secret] = {}
        self._encryption_key: Optional[bytes] = None
        self._init_backend()
    
    def _init_backend(self):
        """Initialize the vault backend"""
        if self.backend == VaultBackend.HASHICORP_VAULT:
            self._init_hashicorp_vault()
        elif self.backend == VaultBackend.AWS_SECRETS_MANAGER:
            self._init_aws_secrets_manager()
        elif self.backend == VaultBackend.AZURE_KEY_VAULT:
            self._init_azure_key_vault()
        elif self.backend == VaultBackend.GOOGLE_SECRET_MANAGER:
            self._init_google_secret_manager()
        else:
            self._init_local_encrypted()
    
    def _init_hashicorp_vault(self):
        """Initialize HashiCorp Vault client"""
        try:
            import hvac
            self._client = hvac.Client(
                url=self.config.get("url", "http://localhost:8200"),
                token=self.config.get("token"),
                namespace=self.config.get("namespace"),
                verify=self.config.get("verify", True)
            )
            # Test connection
            if not self._client.is_authenticated():
                raise Exception("Failed to authenticate with HashiCorp Vault")
        except ImportError:
            raise Exception("hvac library not installed. Install with: pip install hvac")
        except Exception as e:
            raise Exception(f"Failed to initialize HashiCorp Vault: {e}")
    
    def _init_aws_secrets_manager(self):
        """Initialize AWS Secrets Manager client"""
        try:
            import boto3
            self._client = boto3.client(
                'secretsmanager',
                region_name=self.config.get("region", "us-east-1"),
                aws_access_key_id=self.config.get("access_key"),
                aws_secret_access_key=self.config.get("secret_key")
            )
        except ImportError:
            raise Exception("boto3 library not installed. Install with: pip install boto3")
    
    def _init_azure_key_vault(self):
        """Initialize Azure Key Vault client"""
        try:
            from azure.identity import DefaultAzureCredential
            from azure.keyvault.secrets import SecretClient
            credential = DefaultAzureCredential()
            vault_url = self.config.get("vault_url")
            self._client = SecretClient(vault_url=vault_url, credential=credential)
        except ImportError:
            raise Exception("azure-identity and azure-keyvault-secrets not installed")
    
    def _init_google_secret_manager(self):
        """Initialize Google Secret Manager client"""
        try:
            from google.cloud import secretmanager
            self._client = secretmanager.SecretManagerServiceClient()
            self._project_id = self.config.get("project_id")
        except ImportError:
            raise Exception("google-cloud-secret-manager not installed")
    
    def _init_local_encrypted(self):
        """Initialize local encrypted storage (fallback)"""
        # Generate or load encryption key
        key_file = Path(self.config.get("key_file", "./.vault_key"))
        if key_file.exists():
            self._encryption_key = key_file.read_bytes()
        else:
            self._encryption_key = os.urandom(32)
            key_file.write_bytes(self._encryption_key)
            os.chmod(key_file, 0o600)
        
        # Storage directory
        self._storage_dir = Path(self.config.get("storage_dir", "./.vault_secrets"))
        self._storage_dir.mkdir(parents=True, exist_ok=True)
    
    def _encrypt(self, data: str) -> str:
        """Encrypt data using AES-GCM"""
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        aesgcm = AESGCM(self._encryption_key)
        nonce = os.urandom(12)
        ciphertext = aesgcm.encrypt(nonce, data.encode(), None)
        return base64.b64encode(nonce + ciphertext).decode()
    
    def _decrypt(self, encrypted: str) -> str:
        """Decrypt data using AES-GCM"""
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        aesgcm = AESGCM(self._encryption_key)
        data = base64.b64decode(encrypted)
        nonce = data[:12]
        ciphertext = data[12:]
        return aesgcm.decrypt(nonce, ciphertext, None).decode()
    
    def store_secret(self, secret: Secret) -> str:
        """Store a secret in the vault"""
        if self.backend == VaultBackend.HASHICORP_VAULT:
            return self._store_hashicorp(secret)
        elif self.backend == VaultBackend.AWS_SECRETS_MANAGER:
            return self._store_aws(secret)
        elif self.backend == VaultBackend.AZURE_KEY_VAULT:
            return self._store_azure(secret)
        elif self.backend == VaultBackend.GOOGLE_SECRET_MANAGER:
            return self._store_google(secret)
        else:
            return self._store_local(secret)
    
    def get_secret(self, secret_id: str, version: int = None) -> Optional[Secret]:
        """Retrieve a secret from the vault"""
        if self.backend == VaultBackend.HASHICORP_VAULT:
            return self._get_hashicorp(secret_id, version)
        elif self.backend == VaultBackend.AWS_SECRETS_MANAGER:
            return self._get_aws(secret_id, version)
        elif self.backend == VaultBackend.AZURE_KEY_VAULT:
            return self._get_azure(secret_id, version)
        elif self.backend == VaultBackend.GOOGLE_SECRET_MANAGER:
            return self._get_google(secret_id, version)
        else:
            return self._get_local(secret_id, version)
    
    def delete_secret(self, secret_id: str) -> bool:
        """Delete a secret from the vault"""
        # Implementation for each backend
        pass
    
    def list_secrets(self, prefix: str = "") -> List[str]:
        """List secret IDs"""
        pass
    
    def rotate_secret(self, secret_id: str, new_value: str) -> str:
        """Rotate a secret (create new version)"""
        pass
    
    # Local encrypted storage implementations
    def _store_local(self, secret: Secret) -> str:
        secret.updated_at = datetime.utcnow().isoformat()
        file_path = self._storage_dir / f"{secret.id}.enc"
        data = json.dumps(asdict(secret))
        encrypted = self._encrypt(json.dumps(asdict(secret)))
        file_path = self._storage_dir / f"{secret.id}.enc"
        file_path.write_text(encrypted)
        return secret.id
    
    def _get_local(self, secret_id: str, version: int = None) -> Optional[Secret]:
        file_path = self._storage_dir / f"{secret_id}.enc"
        if not file_path.exists():
            return None
        try:
            encrypted = file_path.read_text()
            decrypted = self._decrypt(encrypted)
            data = json.loads(decrypted)
            return Secret(**data)
        except Exception:
            return None
    
    # HashiCorp Vault implementations
    def _store_hashicorp(self, secret: Secret) -> str:
        path = f"secret/data/{secret.id}"
        self._client.secrets.kv.v2.create_or_update_secret(
            path=secret.id,
            secret=asdict(secret)
        )
        return secret.id
    
    def _get_hashicorp(self, secret_id: str, version: int = None) -> Optional[Secret]:
        try:
            if version:
                response = self._client.secrets.kv.v2.read_secret_version(
                    path=secret_id, version=version
                )
            else:
                response = self._client.secrets.kv.v2.read_secret_version(
                    path=secret_id
                )
            data = response['data']['data']
            return Secret(**data)
        except Exception:
            return None
    
    def _store_aws(self, secret: Secret) -> str:
        self._client.put_secret_value(
            SecretId=secret.id,
            SecretString=json.dumps(asdict(secret)),
            VersionStages=['AWSCURRENT']
        )
        return secret.id
    
    def _get_aws(self, secret_id: str, version: int = None) -> Optional[Secret]:
        try:
            kwargs = {'SecretId': secret_id}
            if version:
                kwargs['VersionId'] = version
            response = self._client.get_secret_value(**kwargs)
            data = json.loads(response['SecretString'])
            return Secret(**data)
        except Exception:
            return None
    
    def _store_azure(self, secret: Secret) -> str:
        self._client.set_secret(secret.id, json.dumps(asdict(secret)))
        return secret.id
    
    def _get_azure(self, secret_id: str, version: int = None) -> Optional[Secret]:
        try:
            secret = self._client.get_secret(secret_id)
            data = json.loads(secret.value)
            return Secret(**data)
        except Exception:
            return None
    
    def _store_google(self, secret: Secret) -> str:
        parent = f"projects/{self._project_id}"
        self._client.create_secret(
            request={
                "parent": parent,
                "secret_id": secret.id,
                "secret": {"replication": {"automatic": {}}}
            })
        self._client.add_secret_version(
            request={"parent": f"{parent}/secrets/{secret.id}",
                     "payload": {"data": json.dumps(asdict(secret)).encode()}}
        )
        return secret.id
    
    def _get_google(self, secret_id: str, version: int = None) -> Optional[Secret]:
        try:
            name = f"projects/{self._project_id}/secrets/{secret_id}/versions/{version or 'latest'}"
            response = self._client.access_secret_version(request={"name": secret_id})
            data = json.loads(response.payload.data.decode())
            return Secret(**data)
        except Exception:
            return None


class MTLSManager:
    """
    Manages mTLS certificates for service-to-service authentication.
    Automates certificate generation, rotation, and validation.
    """
    
    def __init__(self, ca_dir: str = "./.mtls_ca", config: Dict = None):
        self.ca_dir = Path(ca_dir)
        self.config = config or {}
        self.ca_dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._ca_cert: Optional[x509.Certificate] = None
        self._ca_key: Optional[rsa.RSAPrivateKey] = None
        self._cert_cache: Dict[str, Certificate] = {}
        self._init_ca()
    
    def _init_ca(self):
        """Initialize or load Certificate Authority"""
        ca_cert_path = self.ca_dir / "ca.crt"
        ca_key_path = self.ca_dir / "ca.key"
        
        if ca_cert_path.exists() and ca_key_path.exists():
            # Load existing CA
            with open(ca_cert_path, "rb") as f:
                self._ca_cert = x509.load_pem_x509_certificate(f.read())
            with open(ca_key_path, "rb") as f:
                self._ca_key = serialization.load_pem_private_key(
                    f.read(), password=None
                )
        else:
            # Generate new CA
            self._generate_ca()
    
    def _generate_ca(self):
        """Generate new Certificate Authority"""
        self._ca_key = rsa.generate_private_key(
            public_exponent=65537,
            key_size=4096
        )
        
        subject = issuer = x509.Name([
            x509.NameAttribute(NameOID.COUNTRY_NAME, "US"),
            x509.NameAttribute(NameOID.ORGANIZATION_NAME, "EMOG"),
            x509.NameAttribute(NameOID.COMMON_NAME, "EMOG Root CA"),
        ])
        
        builder = x509.CertificateBuilder()
        builder = builder.subject_name(subject)
        builder = builder.issuer_name(issuer)
        builder = builder.public_key(self._ca_key.public_key())
        builder = builder.serial_number(x509.random_serial_number())
        builder = builder.not_valid_before(datetime.utcnow())
        builder = builder.not_valid_after(datetime.utcnow() + timedelta(days=3650))  # 10 years
        builder = builder.add_extension(
            x509.BasicConstraints(ca=True, path_length=None),
            critical=True
        )
        builder = builder.add_extension(
            x509.KeyUsage(
                key_cert_sign=True,
                crl_sign=True,
                digital_signature=False,
                content_commitment=False,
                key_encipherment=False,
                data_encipherment=False,
                key_agreement=False,
                encipher_only=False,
                decipher_only=False
            ),
            critical=True
        )
        
        self._ca_cert = builder.sign(
            private_key=self._ca_key,
            algorithm=hashes.SHA256()
        )
        
        # Save CA
        with open(self.ca_dir / "ca.crt", "wb") as f:
            f.write(self._ca_cert.public_bytes(serialization.Encoding.PEM))
        with open(self.ca_dir / "ca.key", "wb") as f:
            f.write(self._ca_key.private_bytes(
                encoding=serialization.Encoding.PEM,
                format=serialization.PrivateFormat.PKCS8,
                encryption_algorithm=serialization.NoEncryption()
            ))
    
    def issue_certificate(self, 
                         common_name: str,
                         sans: List[str] = None,
                         validity_days: int = 365,
                         key_size: int = 2048) -> Certificate:
        """Issue a new certificate signed by CA"""
        with self._lock:
            sans = sans or []
            
            # Generate private key
            key = rsa.generate_private_key(
                public_exponent=65537,
                key_size=key_size
            )
            
            # Build certificate
            subject = x509.Name([
                x509.NameAttribute(NameOID.COUNTRY_NAME, "US"),
                x509.NameAttribute(NameOID.ORGANIZATION_NAME, "EMOG"),
                x509.NameAttribute(NameOID.COMMON_NAME, common_name),
            ])
            
            builder = x509.CertificateBuilder()
            builder = builder.subject_name(subject)
            builder = builder.issuer_name(self._ca_cert.subject)
            builder = builder.public_key(key.public_key())
            builder = builder.serial_number(x509.random_serial_number())
            builder = builder.not_valid_before(datetime.utcnow())
            builder = builder.not_valid_after(datetime.utcnow() + timedelta(days=validity_days))
            
            # Add SAN extension
            if sans:
                san_list = [x509.DNSName(san) for san in sans]
                builder = builder.add_extension(
                    x509.SubjectAlternativeName(san_list),
                    critical=False
                )
            
            builder = builder.add_extension(
                x509.BasicConstraints(ca=False, path_length=None),
                critical=True
            )
            builder = builder.add_extension(
                x509.KeyUsage(
                    digital_signature=True,
                    key_encipherment=True,
                    key_agreement=False,
                    key_cert_sign=False,
                    crl_sign=False,
                    content_commitment=False,
                    data_encipherment=False,
                    encipher_only=False,
                    decipher_only=False
                ),
                critical=True
            )
            builder = builder.add_extension(
                x509.ExtendedKeyUsage([
                    x509.ExtendedKeyUsageOID.SERVER_AUTH,
                    x509.ExtendedKeyUsageOID.CLIENT_AUTH
                ]),
                critical=True
            )
            
            cert = builder.sign(
                private_key=self._ca_key,
                algorithm=hashes.SHA256()
            )
            
            # Serialize
            cert_pem = cert.public_bytes(serialization.Encoding.PEM).decode()
            key_pem = key.private_bytes(
                encoding=serialization.Encoding.PEM,
                format=serialization.PrivateFormat.PKCS8,
                encryption_algorithm=serialization.NoEncryption()
            ).decode()
            ca_pem = self._ca_cert.public_bytes(serialization.Encoding.PEM).decode()
            
            cert_obj = Certificate(
                id=str(uuid.uuid4()),
                common_name=common_name,
                sans=sans,
                not_before=datetime.utcnow().isoformat(),
                not_after=(datetime.utcnow() + timedelta(days=365)).isoformat(),
                cert_pem=cert_pem,
                key_pem=key_pem,
                ca_pem=ca_pem,
                serial_number=str(cert.serial_number),
                fingerprint=cert.fingerprint(hashes.SHA256()).hex()
            )
            
            return cert_obj
    
    def verify_certificate(self, cert_pem: str) -> bool:
        """Verify a certificate against CA"""
        try:
            cert = x509.load_pem_x509_certificate(cert_pem.encode())
            # Verify against CA
            self._ca_cert.public_key().verify(
                cert.signature,
                cert.tbs_certificate_bytes,
                padding.PKCS1v15(),
                cert.signature_hash_algorithm
            )
            # Check validity period
            now = datetime.utcnow()
            if now < cert.not_valid_before or now > cert.not_valid_after:
                return False
            return True
        except Exception:
            return False
    
    def get_ca_pem(self) -> str:
        """Get CA certificate PEM"""
        return self._ca_cert.public_bytes(serialization.Encoding.PEM).decode()


class ZeroTrustManager:
    """
    Central Zero-Trust security manager combining Vault and mTLS.
    Enforces least-privilege access and encrypts all sensitive data.
    """
    
    def __init__(self, 
                 vault_backend: VaultBackend = VaultBackend.LOCAL_ENCRYPTED,
                 vault_config: Dict = None,
                 mtls_config: Dict = None):
        self.vault = VaultClient(vault_backend, vault_config)
        self.mtls = MTLSManager(config=mtls_config)
        self._access_policies: Dict[str, Dict] = {}
        self._audit_log: List[Dict] = []
        self._lock = threading.Lock()
    
    def store_secret(self, 
                    name: str,
                    value: str,
                    secret_type: SecretType = SecretType.GENERIC,
                    metadata: Dict = None,
                    tags: List[str] = None,
                    expires_in_days: int = None) -> Secret:
        """Store a secret securely in the vault"""
        secret = Secret(
            id=str(uuid.uuid4()),
            name=name,
            secret_type=secret_type,
            value=value,
            metadata=metadata or {},
            tags=tags or []
        )
        
        if expires_in_days:
            secret.expires_at = (datetime.utcnow() + timedelta(days=expires_in_days)).isoformat()
        
        secret_id = self.vault.store_secret(secret)
        
        self._audit_log.append({
            "action": "store_secret",
            "secret_id": secret.id,
            "name": name,
            "type": secret_type.value,
            "timestamp": datetime.utcnow().isoformat()
        })
        
        return secret
    
    def get_secret(self, name: str, version: int = None) -> Optional[str]:
        """Retrieve a secret value by name"""
        # First try to find by name
        secrets = self.vault.list_secrets()
        for secret_id in secrets:
            secret = self.vault.get_secret(secret_id)
            if secret and secret.name == name:
                return secret.value
        return None
    
    def require_secret(self, name: str, secret_type: SecretType = SecretType.GENERIC) -> str:
        """
        Get a secret, raising exception if not found.
        Use this instead of hardcoding secrets in code.
        """
        value = self.get_secret(name)
        if value is None:
            raise ValueError(
                f"Secret '{name}' not found in vault. "
                f"Please store it using store_secret() first. "
                f"Type: {secret_type.value}"
            )
        return value
    
    def issue_service_certificate(self, 
                                 service_name: str,
                                 sans: List[str] = None,
                                 validity_days: int = 90) -> Certificate:
        """Issue mTLS certificate for a service"""
        sans = sans or [f"{service_name}.emog.internal", f"{service_name}.local"]
        return self.mtls.issue_certificate(
            common_name=service_name,
            sans=sans,
            validity_days=90
        )
    
    def verify_service_certificate(self, cert_pem: str) -> bool:
        """Verify a service certificate"""
        return self.mtls.verify_certificate(cert_pem)
    
    def create_mtls_context(self, 
                           service_name: str,
                           require_client_cert: bool = True) -> ssl.SSLContext:
        """Create SSL context configured for mTLS"""
        cert = self.issue_service_certificate(service_name)
        
        context = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
        context.load_cert_chain(
            certfile=io.BytesIO(cert.cert_pem.encode()),
            keyfile=io.BytesIO(cert.key_pem.encode())
        )
        context.load_verify_locations(cadata=self.mtls.get_ca_pem())
        
        if require_client_cert:
            context.verify_mode = ssl.CERT_REQUIRED
        else:
            context.verify_mode = ssl.CERT_OPTIONAL
        
        context.minimum_version = ssl.TLSVersion.TLSv1_3
        context.set_ciphers('TLS_AES_256_GCM_SHA384:TLS_CHACHA20_POLY1305_SHA256')
        
        return context
    
    def audit_log(self, action: str, details: Dict) -> None:
        """Log security-relevant actions"""
        with self._lock:
            self._audit_log.append({
                "timestamp": datetime.utcnow().isoformat(),
                "action": action,
                "details": details
            })
    
    def get_audit_log(self, since: datetime = None) -> List[Dict]:
        """Get audit log entries"""
        with self._lock:
            if since:
                return [entry for entry in self._audit_log 
                       if datetime.fromisoformat(entry["timestamp"]) >= since]
            return list(self._audit_log)


# Global instance
_zero_trust: Optional[ZeroTrustManager] = None
_zt_lock = threading.Lock()


def get_zero_trust_manager(vault_backend: VaultBackend = VaultBackend.LOCAL_ENCRYPTED,
                          vault_config: Dict = None,
                          mtls_config: Dict = None) -> ZeroTrustManager:
    """Get or create global Zero-Trust manager"""
    global _zero_trust
    with _zt_lock:
        if _zero_trust is None:
            _zero_trust = ZeroTrustManager(vault_backend, vault_config, mtls_config)
        return _zero_trust


# Convenience functions for common patterns
def store_secret(name: str, value: str, secret_type: SecretType = SecretType.GENERIC, **kwargs) -> Secret:
    """Store a secret using the global Zero-Trust manager"""
    return get_zero_trust_manager().store_secret(name, value, secret_type, **kwargs)


def get_secret(name: str) -> Optional[str]:
    """Get a secret value by name"""
    return get_zero_trust_manager().get_secret(name)


def require_secret(name: str, secret_type: SecretType = SecretType.GENERIC) -> str:
    """Get a secret, raising if not found"""
    return get_zero_trust_manager().require_secret(name, secret_type)


def get_service_certificate(service_name: str, sans: List[str] = None) -> Certificate:
    """Get mTLS certificate for a service"""
    return get_zero_trust_manager().issue_service_certificate(service_name, sans)


def create_mtls_context(service_name: str, require_client: bool = True) -> ssl.SSLContext:
    """Create mTLS SSL context for a service"""
    return get_zero_trust_manager().create_mtls_context(service_name)


# Example usage and initialization
def init_zero_trust(vault_backend: VaultBackend = VaultBackend.LOCAL_ENCRYPTED,
                   vault_config: Dict = None,
                   mtls_config: Dict = None) -> ZeroTrustManager:
    """Initialize Zero-Trust security for the application"""
    zt = get_zero_trust_manager(VaultBackend.LOCAL_ENCRYPTED, {
        "key_file": "./.vault_key",
        "storage_dir": "./.vault_secrets"
    }, {
        "ca_dir": "./.mtls_ca"
    })
    
    # Guvenlik: API anahtarlari ASLA koda gomulmez.
    # Sirlar yalnizca ortam degiskeninden okunur; eksikse vault'a
    # sahte/ornek deger YAZILMAZ, atlanir.
    import os

    env_secrets = [
        ("database_password", "DB_PASSWORD", SecretType.DATABASE_PASSWORD, ["database", "production"]),
        ("jwt_secret", "EMOG_JWT_SECRET", SecretType.JWT_SECRET, ["auth", "jwt"]),
        ("openai_api_key", "OPENAI_API_KEY", SecretType.API_KEY, ["openai", "external-api"]),
        ("anthropic_api_key", "ANTHROPIC_API_KEY", SecretType.API_KEY, ["anthropic", "external-api"]),
        ("github_token", "GITHUB_TOKEN", SecretType.API_KEY, ["github", "external-api"]),
        ("nvidia_api_key", "NVIDIA_API_KEY", SecretType.API_KEY, ["nvidia", "external-api"]),
        ("email_password", "EMAIL_HOST_PASSWORD", SecretType.DATABASE_PASSWORD, ["email", "smtp"]),
    ]

    stored = 0
    skipped = 0

    for name, env_key, secret_type, tags in env_secrets:
        value = os.environ.get(env_key)
        if not value:
            skipped += 1
            continue

        lowered = value.lower()
        if any(p in lowered for p in ("change-in-production", "change-me", "example", "your-")):
            skipped += 1
            continue

        zt.store_secret(name=name, value=value, secret_type=secret_type, tags=tags)
        stored += 1

    if skipped:
        import logging as _logging

        _logging.getLogger(__name__).info(
            "Zero-Trust: %d sir ortam degiskeninden yuklendi, %d tanesi eksik/atlandi",
            stored, skipped,
        )

    return zt


# Context manager for mTLS connections
@contextmanager
def mtls_connection(service_name: str, host: str, port: int, 
                   require_client: bool = True, timeout: int = 30):
    """Context manager for mTLS connections"""
    context = create_mtls_context(service_name, require_client=True)
    
    sock = socket.create_connection((host, port), timeout=timeout)
    secure_sock = context.wrap_socket(sock, server_hostname=service_name)
    
    try:
        yield secure_sock
    finally:
        secure_sock.close()