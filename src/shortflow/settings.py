from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from dotenv import dotenv_values

_SOURCE_ROOT = Path(__file__).resolve().parents[2]
ROOT = Path(os.environ.get('SHORTFLOW_ROOT', str(_SOURCE_ROOT if (_SOURCE_ROOT / 'config').is_dir() else Path.cwd()))).resolve()
PLATFORMS = ("youtube", "tiktok", "instagram", "naver")


@dataclass
class Settings:
    root: Path = ROOT
    values: dict = field(default_factory=dict)

    @classmethod
    def load(cls, root: Path = ROOT):
        values = {**dotenv_values(root / ".env"), **os.environ}
        return cls(root, {k: v for k, v in values.items() if v is not None})

    def get(self, name: str, default=""):
        return self.values.get(name, default)

    @property
    def data_dir(self):
        return Path(self.get("DATA_DIR", str(self.root / "data"))).resolve()

    @property
    def brand(self):
        return self.get("CHANNEL_NAME", "하루의 이득")

    @property
    def sources_path(self):
        return Path(self.get("SOURCES_FILE", str(self.root / "config/sources.json")))

    @property
    def targets(self):
        targets = tuple(t.strip() for t in self.get("UPLOAD_PLATFORMS", ",".join(PLATFORMS)).split(",") if t.strip())
        if not targets or any(t not in PLATFORMS for t in targets):
            raise ValueError("UPLOAD_PLATFORMS에는 youtube,tiktok,instagram,naver만 지정할 수 있습니다.")
        return tuple(dict.fromkeys(targets))

    def integrations(self):
        specs = [
            ("youtube", "YouTube Shorts", ("YOUTUBE_CLIENT_ID", "YOUTUBE_CLIENT_SECRET", "YOUTUBE_REFRESH_TOKEN")),
            ("tiktok", "TikTok", ("TIKTOK_ACCESS_TOKEN",)),
            ("instagram", "Instagram Reels", ("IG_USER_ID", "IG_ACCESS_TOKEN", "PUBLIC_MEDIA_BASE_URL", "MEDIA_SIGNING_KEY")),
            ("naver", "네이버 클립", ()),
        ]
        result = []
        for name, label, keys in specs:
            missing = [k for k in keys if not self.get(k)]
            if name == "youtube" and self.get("YOUTUBE_ACCESS_TOKEN"):
                missing = []
            if name == "naver":
                status, detail = "manual_required", "공개 업로드 API 미확인 · 영상/설명 패키지 직접 업로드"
            elif name == 'tiktok' and self.get('TIKTOK_PUBLISHING_MODE', 'manual') != 'approved_app':
                status, detail = 'manual_required', '개인·팀 전용 내부 업로더는 Direct Post 허용 용도에서 제외 · 승인된 별도 연동 필요'
            elif missing:
                status, detail = "not_configured", "설정 필요: " + ", ".join(missing)
            elif name == "tiktok" and str(self.get("TIKTOK_APP_APPROVED")).lower() != "true":
                status, detail = "approval_required", "Direct Post 앱 심사와 게시 권한 확인 필요"
            else:
                status, detail = "configured", "설정 입력됨 · 실제 계정 게시 검증 전"
            result.append(dict(name=name, label=label, status=status, detail=detail))
        return result
