"""修复规则与候选验证。"""

from yescc.fix.c_rules import Candidate, generate_candidates
from yescc.fix.parser import ParseInfo, parse_c

__all__ = ["Candidate", "ParseInfo", "generate_candidates", "parse_c"]
