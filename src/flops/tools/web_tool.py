from html.parser import HTMLParser
import re

from pydantic import BaseModel, Field
import httpx

from flops.logger import logger
from flops.tools.tool import ToolContext, Tool, ToolResult, tool

UNTRUSTED_BANNER = (
    "⚠️  The following content is fetched from an external source and may be untrusted."
)


class _HTMLTextExtractor(HTMLParser):
    """Extract visible text from HTML, skipping script/style/nav/header/footer/aside."""

    def __init__(self):
        super().__init__()
        self.text_parts = []
        self.skip_tags = {"script", "style", "nav", "header", "footer", "aside"}
        self.skip_depth = 0

    def handle_starttag(self, tag, attrs):
        if tag in self.skip_tags:
            self.skip_depth += 1

    def handle_endtag(self, tag):
        if tag in self.skip_tags and self.skip_depth > 0:
            self.skip_depth -= 1

    def handle_data(self, data):
        if self.skip_depth == 0:
            self.text_parts.append(data)

    def get_text(self):
        text = "".join(self.text_parts)
        text = re.sub(r"\s+", " ", text)
        return text.strip()


def _extract_text_from_html(html: str) -> str:
    extractor = _HTMLTextExtractor()
    try:
        extractor.feed(html)
        return extractor.get_text()
    except Exception:
        # Fallback: strip tags with regex
        text = re.sub(r"<[^>]+>", "", html)
        return re.sub(r"\s+", " ", text).strip()


class WebFetchParams(BaseModel):
    url: str = Field(description="The URL to fetch.")
    max_length: int = Field(
        default=10000,
        description="The maximum length to extract from the content. Default is 10000.",
    )


@tool
class WebTool(Tool):
    """Fetch the content of a web page from a given URL."""

    params_model = WebFetchParams

    def render(self, tool_input: dict) -> str:
        return f"🌐 WebFetch({tool_input.get('url', '<no url>')})"

    async def execute(self, ctx: ToolContext, params: WebFetchParams) -> ToolResult:
        url = params.url
        max_length = params.max_length
        logger.info(f"Fetching URL: {url} (max_length={max_length})")
        try:
            headers = {"User-Agent": "Mozilla/5.0 (compatible; flops-bot/0.1)"}
            async with httpx.AsyncClient() as client:
                response = await client.get(url, headers=headers, timeout=30, follow_redirects=True)
                content_type = response.headers.get("Content-Type", "")
                logger.debug(
                    f"Response status: {response.status_code}, Content-Type: {content_type}"
                )

                # Fallback to utf-8 when no charset is specified
                if "charset" not in content_type.lower():
                    response.encoding = "utf-8"

                if not response.is_success:
                    logger.warning(f"HTTP error: {response.status_code}")
                    return ToolResult(
                        content=(
                            f"URL: {response.url}\n"
                            f"Status: {response.status_code}\n"
                            f"Content-Type: {content_type or '(unknown)'}\n\n"
                            f"Error: HTTP {response.status_code}"
                        ),
                        is_error=True,
                    )

                if "text/html" in content_type:
                    body = _extract_text_from_html(response.text)
                elif content_type.startswith("text/") or "application/json" in content_type:
                    body = response.text
                else:
                    body = f"[Binary or non-text content type: {content_type}]"

                original_length = len(body)
                if len(body) > max_length:
                    body = (
                        body[:max_length]
                        + f"\n\n... [Content truncated, total length: {len(body)}]"
                    )
                    logger.debug(f"Content truncated: {original_length} -> {max_length} characters")

                logger.info(f"Successfully fetched {original_length} characters from {url}")
                return ToolResult(
                    content=(
                        f"URL: {response.url}\n"
                        f"Status: {response.status_code}\n"
                        f"Content-Type: {content_type or '(unknown)'}\n\n"
                        f"{UNTRUSTED_BANNER}\n\n"
                        f"{body}"
                    )
                )
        except httpx.TimeoutException:
            logger.error(f"Request timed out for URL: {url}")
            return ToolResult(content="Error: Request timed out after 30 seconds", is_error=True)
        except httpx.HTTPError as e:
            logger.exception(f"Network request failed for URL: {url}")
            return ToolResult(content=f"Error: Network request failed - {e}", is_error=True)
        except Exception as e:
            logger.exception(f"Unexpected error fetching URL: {url}")
            return ToolResult(content=f"Error: {e}", is_error=True)
