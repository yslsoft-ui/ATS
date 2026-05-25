import asyncio
import aiohttp
import time
from src.engine.utils.telemetry import get_logger
import json
import os
from typing import Dict, Optional

logger = get_logger(__name__)

class CredentialProvider:
    """
    다중 시장 API 인증 정보 및 토큰을 중앙 관리하는 프로바이더입니다.
    KIS(한국투자증권)의 Access Token 및 Approval Key의 자동 갱신을 담당합니다.
    """
    _instance = None

    def __new__(cls, *args, **kwargs):
        if not cls._instance:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self, config: Dict = None):
        if config is not None:
            self.config = config
        if hasattr(self, '_initialized'):
            return
        if config is None:
            self.config = {}
        self.kis_token: Optional[str] = None
        self.kis_token_expires: float = 0
        self.kis_token_last_attempt: float = 0 # 추가: 마지막 시도 시간
        self.kis_approval_key: Optional[str] = None
        self._initialized = True
        self.session: Optional[aiohttp.ClientSession] = None
        self.last_error: Optional[str] = None
        self.last_status: Optional[int] = None
        
        # 추가: 저장된 토큰 로드
        self.token_file = os.path.join(os.getcwd(), 'config', '.kis_token.json')
        self._load_token()

    def _load_token(self):
        """저장된 토큰 정보를 파일에서 읽어옵니다."""
        if os.path.exists(self.token_file):
            try:
                with open(self.token_file, 'r') as f:
                    data = json.load(f)
                    self.kis_token = data.get('access_token')
                    self.kis_token_expires = data.get('expires_at', 0)
                    if self.kis_token and time.time() < self.kis_token_expires - 60:
                        logger.info(f"Loaded valid token from {self.token_file}")
                    else:
                        logger.info("Cached token is expired.")
            except Exception as e:
                logger.error(f"Failed to load token file: {e}")
        else:
            logger.info(f"No cached token file found at {self.token_file}")

    def _save_token(self, token: str, expires_in: int):
        """발급받은 토큰 정보를 파일에서 저장합니다."""
        try:
            os.makedirs(os.path.dirname(self.token_file), exist_ok=True)
            data = {
                'access_token': token,
                'expires_at': time.time() + expires_in,
                'saved_at': time.time()
            }
            with open(self.token_file, 'w') as f:
                json.dump(data, f)
            logger.info("Token saved to file for reuse.")
        except Exception as e:
            logger.error(f"Failed to save token file: {e}")

    async def get_kis_access_token(self) -> Optional[str]:
        """한국투자증권 접근 토큰을 반환합니다. 만료 시 자동 재발급합니다."""
        # 1. 이미 유효한 토큰이 있으면 반환
        if self.kis_token and time.time() < self.kis_token_expires - 60:
            logger.info("Using valid memory-cached token.")
            return self.kis_token

        # 2. 1분 이내에 실패한 기록이 있으면 재시도 차단 (Rate Limit 대응)
        elapsed = time.time() - self.kis_token_last_attempt
        if elapsed < 60:
            logger.warning(f"Token request cooldown in effect. {int(60 - elapsed)}s remaining.")
            return None

        self.kis_token_last_attempt = time.time()
        return await self._refresh_kis_token()

    async def get_kis_approval_key(self) -> Optional[str]:
        """웹소켓 연결용 승인키를 반환합니다."""
        if self.kis_approval_key:
            return self.kis_approval_key
        
        return await self._refresh_kis_approval_key()

    async def _refresh_kis_token(self) -> Optional[str]:
        """한국투자증권 접근 토큰 발급"""
        self.last_error = None # 에러 초기화
        kis_config = self.config.get('exchanges', {}).get('kis', {})
        
        app_key = str(kis_config.get('app_key', '')).strip()
        app_secret = str(kis_config.get('app_secret', '')).strip()
        api_url = str(kis_config.get('api_url', 'https://openapi.koreainvestment.com:9443')).strip()

        if not app_key or not app_secret:
            self.last_error = f"KIS 인증 정보 누락 (Key length: {len(app_key)}, Secret length: {len(app_secret)})"
            logger.error(self.last_error)
            return None

        url = api_url
        path = "/oauth2/tokenP"
        
        payload = {
            "grant_type": "client_credentials",
            "appkey": app_key,
            "appsecret": app_secret
        }

        try:
            if not self.session:
                self.session = aiohttp.ClientSession()
            
            async with self.session.post(f"{url}{path}", json=payload) as resp:
                self.last_status = resp.status
                if resp.status == 200:
                    data = await resp.json()
                    self.kis_token = data.get('access_token')
                    expires_in = data.get('expires_in', 86400)
                    self.kis_token_expires = time.time() + expires_in
                    
                    # 파일에 저장
                    self._save_token(self.kis_token, expires_in)
                    
                    logger.info("KIS Access Token refreshed successfully.")
                    return self.kis_token
                else:
                    error_msg = await resp.text()
                    self.last_error = f"KIS Token Error: {resp.status} - {error_msg}"
                    logger.error(self.last_error)
        except Exception as e:
            self.last_error = f"KIS Token Exception: {str(e)}"
            logger.error(self.last_error)
        
        return None

    async def _refresh_kis_approval_key(self) -> Optional[str]:
        """웹소켓 실시간 데이터 수집용 승인키 발급"""
        kis_config = self.config.get('exchanges', {}).get('kis', {})
        
        app_key = str(kis_config.get('app_key', '')).strip()
        app_secret = str(kis_config.get('app_secret', '')).strip()
        api_url = str(kis_config.get('api_url', 'https://openapi.koreainvestment.com:9443')).strip()

        url = api_url
        path = "/oauth2/Approval"
        
        payload = {
            "grant_type": "client_credentials",
            "appkey": app_key,
            "secretkey": app_secret
        }

        try:
            if not self.session:
                self.session = aiohttp.ClientSession()

            async with self.session.post(f"{url}{path}", json=payload) as resp:
                self.last_status = resp.status
                if resp.status == 200:
                    data = await resp.json()
                    self.kis_approval_key = data.get('approval_key')
                    logger.info("KIS Approval Key (Websocket) acquired.")
                    return self.kis_approval_key
                else:
                    error_msg = await resp.text()
                    self.last_error = f"KIS Approval Error: {resp.status} - {error_msg}"
                    logger.error(self.last_error)
        except Exception as e:
            self.last_error = f"KIS Approval Exception: {str(e)}"
            logger.error(self.last_error)
        
        return None

    async def close(self):
        if self.session:
            await self.session.close()
            self.session = None
