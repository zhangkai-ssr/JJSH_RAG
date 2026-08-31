"""验证本机运行 JSSH RAG 所需的标准库能力。"""

import sqlite3
import unittest


class PreflightTest(unittest.TestCase):
    """检查 SQLite 全文索引可用性。"""

    def test_sqlite_fts5_available(self):
        connection = sqlite3.connect(":memory:")
        self.addCleanup(connection.close)
        connection.execute("CREATE VIRTUAL TABLE chunks_fts USING fts5(content)")


if __name__ == "__main__":
    unittest.main()
