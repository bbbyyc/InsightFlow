import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

from app.database import Base
from app.models import Document, DocumentStatus, Chunk
from app.parsers.pdf_parser import PDFParser, ParsedPDFText
from app.parsers.base import ChunkData
from app.parsers.quality import DocumentQualityError, validate_extracted_text
from app.services.document_service import DocumentService


class QualityTests(unittest.TestCase):
    def test_cid_replacement_and_empty_rejected(self):
        for text in ["(cid:123)" * 40 + "SELECT", "\ufffd" * 20 + "text", " \n\f"]:
            with self.subTest(text=text), self.assertRaises(DocumentQualityError):
                validate_extracted_text(text)

    def test_sparse_symbols_and_valid_chinese_accepted(self):
        validate_extracted_text("中文正文、SQL SELECT、数学公式 ∫ δ ε。" * 20 + "(cid:123)")
        validate_extracted_text("短文")

    def test_blank_pdf_page_preserves_citation_number(self):
        pdf = MagicMock()
        pdf.__enter__.return_value.pages = [
            SimpleNamespace(extract_text=lambda: "第一页"),
            SimpleNamespace(extract_text=lambda: None),
            SimpleNamespace(extract_text=lambda: "第三页"),
        ]
        with patch("app.parsers.pdf_parser.pdfplumber.open", return_value=pdf):
            parser = PDFParser()
            chunks = parser.chunk(parser.parse("unused"))
        self.assertEqual([c.page_number for c in chunks], [1, 3])

    def test_single_corrupt_page_not_diluted_by_good_pages(self):
        pdf = MagicMock()
        pdf.__enter__.return_value.pages = [
            SimpleNamespace(extract_text=lambda: "正常正文" * 1000),
            SimpleNamespace(extract_text=lambda: "(cid:123)" * 30),
        ]
        with (patch("app.parsers.pdf_parser.pdfplumber.open", return_value=pdf),
              patch("app.parsers.pdf_parser.settings.pdf_ocr_enabled", False)):
            with self.assertRaisesRegex(DocumentQualityError, "第 2 页"):
                PDFParser().parse("unused")


class QualityPersistenceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.engine = create_async_engine("sqlite+aiosqlite:///" + (Path(self.temp.name) / "quality.db").as_posix())
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)

    async def asyncTearDown(self):
        await self.engine.dispose()
        self.temp.cleanup()

    async def test_invalid_new_and_legacy_documents_fail_before_embedding(self):
        for legacy in [False, True]:
            async with self.sessions() as db:
                doc = Document(title="bad.pdf", file_type="pdf", file_path="unused")
                db.add(doc)
                await db.flush()
                if legacy:
                    db.add(Chunk(document_id=doc.id, chunk_index=0, content="(cid:123)" * 30))
                await db.commit()
                service = DocumentService(db)
                with (
                    patch("app.services.document_service.get_parser") as parser,
                    patch.object(service, "_generate_embeddings", new_callable=AsyncMock) as embed,
                ):
                    parser.return_value.parse.return_value = "(cid:123)" * 30
                    with self.assertRaises(DocumentQualityError):
                        await service.process_document(str(doc.id))
                    embed.assert_not_awaited()
                    self.assertEqual(doc.status, DocumentStatus.FAILED)
                    self.assertIn("DocumentQualityError", doc.error_message)
                    self.assertEqual(len(await service.get_chunks(str(doc.id))), int(legacy))

    async def test_empty_chunk_output_cannot_complete(self):
        async with self.sessions() as db:
            doc = Document(title="empty.md", file_type="md", file_path="unused")
            db.add(doc)
            await db.commit()
            with patch("app.services.document_service.get_parser") as parser:
                parser.return_value.parse.return_value = "nonempty"
                parser.return_value.chunk.return_value = []
                with self.assertRaises(DocumentQualityError):
                    await DocumentService(db).process_document(str(doc.id))
            self.assertEqual(doc.status, DocumentStatus.FAILED)

    async def test_legacy_repair_replaces_old_vectors_and_persists_report(self):
        async with self.sessions() as db:
            doc = Document(title="bad.pdf", file_type="pdf", file_path="unused", status=DocumentStatus.FAILED)
            db.add(doc)
            await db.flush()
            old = Chunk(document_id=doc.id, chunk_index=0, content="(cid:12)"*30,
                        embedding=[0.1]*384, embedding_model="old-model")
            db.add(old)
            await db.commit()
            old_id = old.id
            report = {"status": "completed", "ocr_pages": 1, "pages": [{"page_number": 1, "method": "ocr"}]}
            service = DocumentService(db)
            async def embed(document_id):
                chunks = await service.get_chunks(str(document_id))
                self.assertEqual(len(chunks), 1)
                self.assertNotEqual(chunks[0].id, old_id)
                self.assertIsNone(chunks[0].embedding)
                self.assertEqual(chunks[0].content, "CREATE TABLE 学生表")
            with (patch("app.services.document_service.get_parser") as parser,
                  patch.object(service, "_generate_embeddings", side_effect=embed)):
                parser.return_value.parse.return_value = ParsedPDFText("CREATE TABLE 学生表", report)
                parser.return_value.chunk.return_value = [ChunkData("CREATE TABLE 学生表", 0, page_number=1)]
                await service.process_document(str(doc.id))
            self.assertEqual(doc.status, DocumentStatus.COMPLETED)
            self.assertEqual(doc.extraction_report, report)
            self.assertIsNone(doc.error_message)
