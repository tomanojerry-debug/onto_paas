"""Extract bounded, source-labelled text from business requirements attachments."""

from hashlib import sha256
from io import BytesIO
from pathlib import Path


MAX_FILE_BYTES = 8 * 1024 * 1024
MAX_TEXT_LENGTH = 200_000
MAX_PDF_PAGES = 100


def read_document(upload):
    name = (upload.filename or '').replace('\\', '/').rsplit('/', 1)[-1].strip()
    extension = Path(name).suffix.lower()
    if not name or len(name) > 180 or extension not in {'.md', '.markdown', '.txt', '.pdf'}:
        raise ValueError('请选择 Markdown、TXT 或 PDF 需求文件')
    raw = upload.stream.read(MAX_FILE_BYTES + 1)
    if not raw or len(raw) > MAX_FILE_BYTES:
        raise ValueError(f'{name} 需为非空文件，且不超过 8 MB')
    pages = None
    if extension == '.pdf':
        from pypdf import PdfReader
        try:
            reader = PdfReader(BytesIO(raw))
            if reader.is_encrypted:
                raise ValueError(f'{name} 是加密的 PDF，请上传已解密的文件')
            pages = len(reader.pages)
            if not 1 <= pages <= MAX_PDF_PAGES:
                raise ValueError(f'{name} 的页数须为 1 至 {MAX_PDF_PAGES} 页')
            parts = []
            size = 0
            for index, page in enumerate(reader.pages, 1):
                content = (page.extract_text() or '').strip()
                if content:
                    parts.append(f'### PDF 第 {index} 页\n\n{content}')
                    size += len(parts[-1]) + 2
                    if size > MAX_TEXT_LENGTH:
                        raise ValueError(f'{name} 提取后超过 {MAX_TEXT_LENGTH} 字符，请拆分上传')
            text = '\n\n'.join(parts)
        except ValueError:
            raise
        except Exception as error:
            raise ValueError(f'无法读取 PDF {name}，请检查文件是否完整') from error
        if not text:
            raise ValueError(f'{name} 没有可提取的文字，请上传文字版 PDF 或 Markdown')
    else:
        try:
            text = raw.decode('utf-8-sig').strip()
        except UnicodeDecodeError:
            raise ValueError(f'{name} 请使用 UTF-8 编码') from None
    if not text or len(text) > MAX_TEXT_LENGTH:
        raise ValueError(f'{name} 的正文需为 1 至 {MAX_TEXT_LENGTH} 个字符')
    return text, {'name': name, 'sha256': sha256(raw).hexdigest(), 'pages': pages}
