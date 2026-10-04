from sqlalchemy import BigInteger, Column, Integer

from app.database import Base


class CorpusRevision(Base):
    __tablename__ = "corpus_revision"

    id = Column(Integer, primary_key=True)
    revision = Column(BigInteger, nullable=False)
