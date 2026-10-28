"""Plain dataclasses for the pipeline. No third-party validation on purpose."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

WM_PREFIX_RE = re.compile(r"^\s*\[([^\]\n]{1,40})\]")

# r/unixporn also uses [...] for post types, not window managers. "[OC] neoclear"
# must not be reported as a Window Manager called "OC".
NON_WM_PREFIXES = frozenset(
    {
        "oc", "meta", "question", "help", "discussion", "resource", "resources",
        "guide", "news", "article", "tip", "tips", "showcase", "update", "release",
        "announcement", "review", "rant", "poll", "idea", "request", "solved",
        "tutorial", "video", "wallpaper",
    }
)


def parse_wm_from_title(title: str) -> str | None:
    """r/unixporn convention: '[Hyprland] my rice' -> 'Hyprland'.

    Post-type tags such as ``[OC]`` are skipped; ``[OC] [Hyprland] x`` -> Hyprland.
    """
    rest = title or ""
    while match := WM_PREFIX_RE.match(rest):
        candidate = match.group(1).strip()
        if candidate and candidate.lower() not in NON_WM_PREFIXES:
            return candidate
        rest = rest[match.end() :]
    return None


@dataclass(slots=True)
class Post:
    id: str  # short reddit id, e.g. "1xeuuwb"
    title: str
    author: str  # without the /u/ prefix
    created_utc: int
    permalink: str
    selftext: str = ""
    image_url: str | None = None
    links: list[str] = field(default_factory=list)
    wm: str | None = None
    window: str = "week"

    def __post_init__(self) -> None:
        if self.wm is None:
            self.wm = parse_wm_from_title(self.title)

    @property
    def url(self) -> str:
        if self.permalink.startswith("http"):
            return self.permalink
        return f"https://www.reddit.com{self.permalink}"


@dataclass(slots=True)
class Comment:
    id: str
    post_id: str
    author: str
    body: str
    ordinal: int = 0


@dataclass(slots=True, frozen=True)
class PaletteColor:
    hex: str
    share: float

    def as_dict(self) -> dict[str, Any]:
        return {"hex": self.hex, "share": round(self.share, 4)}


@dataclass(slots=True)
class Palette:
    colors: list[PaletteColor] = field(default_factory=list)
    dark: bool = True
    bg_hex: str = "#000000"

    @property
    def hexes(self) -> list[str]:
        return [c.hex for c in self.colors]

    def as_dict(self) -> dict[str, Any]:
        return {
            "colors": [c.as_dict() for c in self.colors],
            "dark": self.dark,
            "bg_hex": self.bg_hex,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> Palette:
        if not data:
            return cls()
        colors = [
            PaletteColor(hex=str(c.get("hex", "#000000")), share=float(c.get("share", 0.0)))
            for c in data.get("colors", [])
            if isinstance(c, dict) and c.get("hex")
        ]
        return cls(
            colors=colors,
            dark=bool(data.get("dark", True)),
            bg_hex=str(data.get("bg_hex") or (colors[0].hex if colors else "#000000")),
        )


@dataclass(slots=True)
class Component:
    name: str
    detail: str

    def as_dict(self) -> dict[str, str]:
        return {"name": self.name, "detail": self.detail}


DIFFICULTIES = ("easy", "medium", "hard")


@dataclass(slots=True)
class RiceCard:
    """The LLM-produced, user-facing summary of one rice post."""

    post_id: str
    title_zh: str
    summary_zh: str
    wm: str | None = None
    distro: str | None = None
    theme: str | None = None
    colors: list[str] = field(default_factory=list)
    fonts: list[str] = field(default_factory=list)
    components: list[Component] = field(default_factory=list)
    dots_url: str | None = None
    difficulty: str = "medium"
    highlights: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    worth_reproducing: bool = True
    llm_error: bool = False
    model: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "post_id": self.post_id,
            "title_zh": self.title_zh,
            "summary_zh": self.summary_zh,
            "wm": self.wm,
            "distro": self.distro,
            "theme": self.theme,
            "colors": list(self.colors),
            "fonts": list(self.fonts),
            "components": [c.as_dict() for c in self.components],
            "dots_url": self.dots_url,
            "difficulty": self.difficulty,
            "highlights": list(self.highlights),
            "tags": list(self.tags),
            "worth_reproducing": self.worth_reproducing,
            "llm_error": self.llm_error,
            "model": self.model,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> RiceCard:
        components = [
            Component(name=str(c.get("name", "")), detail=str(c.get("detail", "")))
            for c in data.get("components") or []
            if isinstance(c, dict)
        ]
        difficulty = str(data.get("difficulty") or "medium").lower()
        return cls(
            post_id=str(data.get("post_id", "")),
            title_zh=str(data.get("title_zh") or ""),
            summary_zh=str(data.get("summary_zh") or ""),
            wm=data.get("wm") or None,
            distro=data.get("distro") or None,
            theme=data.get("theme") or None,
            colors=[str(c) for c in data.get("colors") or []],
            fonts=[str(f) for f in data.get("fonts") or []],
            components=components,
            dots_url=data.get("dots_url") or None,
            difficulty=difficulty if difficulty in DIFFICULTIES else "medium",
            highlights=[str(h) for h in data.get("highlights") or []],
            tags=[str(t) for t in data.get("tags") or []],
            worth_reproducing=bool(data.get("worth_reproducing", True)),
            llm_error=bool(data.get("llm_error", False)),
            model=str(data.get("model") or ""),
        )
