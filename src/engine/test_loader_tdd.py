import unittest
import os
import shutil
from src.engine.loader import load_dynamic_strategies, unload_strategy, StrategyRegistry

class TestStrategyLoaderTDD(unittest.TestCase):
    def setUp(self):
        # 테스트용 임시 디렉토리 생성
        self.test_dir = os.path.join(os.getcwd(), 'data', 'test_strategies')
        if not os.path.exists(self.test_dir):
            os.makedirs(self.test_dir)
        # 레지스트리 초기화 (테스트 간 간섭 방지)
        StrategyRegistry._strategies = {}

    def tearDown(self):
        # 테스트 후 임시 디렉토리 삭제
        if os.path.exists(self.test_dir):
            shutil.rmtree(self.test_dir)

    def test_load_invalid_syntax_file(self):
        """문법 오류가 있는 파일을 로드할 때 시스템이 중단되지 않는지 확인합니다."""
        broken_file_path = os.path.join(self.test_dir, 'broken_strategy.py')
        with open(broken_file_path, 'w', encoding='utf-8') as f:
            f.write("this is not valid python code!!!\n")
            f.write("def broken_function(:") # 문법 오류
        
        # 로드 실행
        count = load_dynamic_strategies(self.test_dir)
        
        self.assertEqual(count, 0)
        self.assertEqual(len(StrategyRegistry._strategies), 0)

    def test_unload_strategy_removes_file(self):
        """전략 해제 시 레지스트리 제거 및 파일 삭제가 이루어지는지 확인합니다."""
        file_path = os.path.join(self.test_dir, 'to_be_deleted.py')
        with open(file_path, 'w', encoding='utf-8') as f:
            f.write("from src.engine.strategy import BaseStrategy, StrategyRegistry\n")
            f.write("@StrategyRegistry.register\n")
            f.write("class DeleteMe(BaseStrategy):\n")
            f.write("    def on_candle(self, c): pass\n")
        
        # 1. 로드
        load_dynamic_strategies(self.test_dir)
        self.assertIn('deleteme', StrategyRegistry._strategies)
        self.assertTrue(os.path.exists(file_path))
        
        # 2. 해제 (Unload)
        result = unload_strategy('deleteme')
        
        # 3. 검증
        self.assertTrue(result)
        self.assertNotIn('deleteme', StrategyRegistry._strategies)
        self.assertFalse(os.path.exists(file_path), "물리 파일이 삭제되어야 합니다.")

    def test_duplicate_strategy_loading(self):
        """동일한 클래스명을 가진 전략이 여러 파일에 있을 때의 처리를 확인합니다."""
        file1 = os.path.join(self.test_dir, 'strat1.py')
        file2 = os.path.join(self.test_dir, 'strat2.py')
        
        content = (
            "from src.engine.strategy import BaseStrategy, StrategyRegistry\n"
            "@StrategyRegistry.register\n"
            "class DuplicateStrat(BaseStrategy):\n"
            "    def on_candle(self, c): pass\n"
        )
        
        with open(file1, 'w') as f: f.write(content)
        with open(file2, 'w') as f: f.write(content)
        
        # 로드 실행
        count = load_dynamic_strategies(self.test_dir)
        
        # 레지스트리에는 1개만 남아야 함 (마지막 파일이 덮어쓰거나 무시됨)
        self.assertEqual(len(StrategyRegistry._strategies), 1)

if __name__ == '__main__':
    unittest.main()
