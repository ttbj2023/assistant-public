"""测试数据工厂基类

提供所有工厂类的基础功能和通用方法。
"""

from __future__ import annotations

import random
import uuid
from abc import ABC, abstractmethod
from datetime import UTC, datetime, timedelta
from typing import Any


class BaseFactory(ABC):
    """测试数据工厂基类"""

    def __init__(self):
        """初始化工厂"""
        self._sequences: dict[str, int] = {}
        self._faker: Any | None = None

    @abstractmethod
    def create(self, **kwargs) -> dict[str, Any]:
        """创建数据 - 子类必须实现"""
        raise NotImplementedError("子类必须实现create方法")

    def get_sequence(self, name: str) -> int:
        """获取序列号"""
        if name not in self._sequences:
            self._sequences[name] = 0
        self._sequences[name] += 1
        return self._sequences[name]

    def reset_sequences(self):
        """重置所有序列号"""
        self._sequences.clear()

    def faker(self):
        """获取Faker实例"""
        if self._faker is None:
            self._faker = self._create_faker()
        return self._faker

    def _create_faker(self):
        """创建Faker实例"""
        try:
            from faker import Faker

            faker = Faker("zh_CN")
            faker.seed_instance(12345)  # 固定种子确保可重现性
            return faker
        except ImportError:
            # 如果没有安装faker，使用简单的模拟数据
            return SimpleFaker()

    def generate_uuid(self) -> str:
        """生成UUID"""
        return str(uuid.uuid4())

    def generate_timestamp(
        self, days_ago: int | None = None, hours_offset: int | None = None
    ) -> datetime:
        """生成时间戳"""
        base_time = datetime.now(UTC)

        if days_ago is not None:
            base_time -= timedelta(days=days_ago)

        if hours_offset is not None:
            base_time += timedelta(hours=hours_offset)

        return base_time

    def generate_random_choice(self, choices: list[Any]) -> Any:
        """从列表中随机选择"""
        return random.choice(choices)

    def generate_random_int(self, min_val: int = 1, max_val: int = 1000) -> int:
        """生成随机整数"""
        return random.randint(min_val, max_val)

    def generate_random_float(
        self, min_val: float = 0.0, max_val: float = 100.0, decimals: int = 2
    ) -> float:
        """生成随机浮点数"""
        return round(random.uniform(min_val, max_val), decimals)

    def generate_random_string(self, length: int = 10, prefix: str = "") -> str:
        """生成随机字符串"""
        import string

        chars = string.ascii_letters + string.digits
        random_part = "".join(random.choice(chars) for _ in range(length))
        return prefix + random_part

    def generate_boolean(self, true_probability: float = 0.5) -> bool:
        """生成布尔值"""
        return random.random() < true_probability

    def generate_weighted_choice(self, choices: list[Any], weights: list[float]) -> Any:
        """根据权重生成随机选择"""
        return random.choices(choices, weights=weights)[0]


class SimpleFaker:
    """简单的数据生成器（当Faker不可用时的后备方案）"""

    def __init__(self):
        self._counters: dict[str, int] = {}
        self._names = ["张三", "李四", "王五", "赵六", "钱七", "孙八", "周九", "吴十"]
        self._emails = ["test.com", "example.com", "demo.org", "sample.net"]
        self._words = [
            "测试",
            "数据",
            "系统",
            "功能",
            "模块",
            "接口",
            "服务",
            "平台",
            "应用",
            "工具",
        ]

    def name(self) -> str:
        """生成姓名"""
        self._counters["name"] = self._counters.get("name", 0) + 1
        return self._names[self._counters["name"] % len(self._names)]

    def email(self) -> str:
        """生成邮箱"""
        self._counters["email"] = self._counters.get("email", 0) + 1
        username = f"user{self._counters['email']}"
        domain = random.choice(self._emails)
        return f"{username}@{domain}"

    def text(self, max_nb_chars: int = 200) -> str:
        """生成文本"""
        self._counters["text"] = self._counters.get("text", 0) + 1
        words_needed = max_nb_chars // 10  # 假设平均每个词10个字符

        result = []
        for i in range(words_needed):
            word = random.choice(self._words)
            result.append(word)
            if i % 5 == 0:  # 每5个词添加一个数字
                result.append(str(self._counters["text"]))

        return " ".join(result)[:max_nb_chars]

    def sentence(self) -> str:
        """生成句子"""
        self._counters["sentence"] = self._counters.get("sentence", 0) + 1
        templates = [
            "这是第{}个测试句子。",
            "系统功能{}运行正常。",
            "用户{}完成了操作。",
            "数据{}验证通过。",
            "接口{}调用成功。",
        ]
        template = random.choice(templates)
        return template.format(self._counters["sentence"])

    def paragraph(self) -> str:
        """生成段落"""
        self._counters["paragraph"] = self._counters.get("paragraph", 0) + 1
        sentences = [self.sentence(), self.sentence(), self.sentence()]
        return " ".join(sentences)

    def datetime(self) -> datetime:
        """生成日期时间"""
        self._counters["datetime"] = self._counters.get("datetime", 0) + 1
        days_ago = (self._counters["datetime"] * 7) % 365
        return datetime.now(UTC) - timedelta(days=days_ago)

    def date_time_this_year(self) -> datetime:
        """生成今年的日期时间"""
        return self.datetime()

    def date_between(self, start_date: str, end_date: str) -> str:
        """生成日期范围内的日期"""
        # 简化实现，返回固定格式日期
        self._counters["date_between"] = self._counters.get("date_between", 0) + 1
        days_offset = (self._counters["date_between"] * 3) % 30
        target_date = datetime.now(UTC) + timedelta(days=days_offset)
        return target_date.strftime("%Y-%m-%d")

    def uuid4(self) -> str:
        """生成UUID"""
        return str(uuid.uuid4())

    def random_int(self, min: int = 1, max: int = 1000) -> int:
        """生成随机整数"""
        return random.randint(min, max)

    def random_element(self, elements: list):
        """从列表中随机选择元素"""
        return random.choice(elements)

    def word(self) -> str:
        """生成单词"""
        return random.choice(self._words)

    def words(self, nb: int = 3) -> list:
        """生成多个单词"""
        return random.choices(self._words, k=nb)

    def url(self) -> str:
        """生成URL"""
        self._counters["url"] = self._counters.get("url", 0) + 1
        domains = ["example.com", "test.org", "demo.net", "sample.app"]
        path = f"/path/{self._counters['url']}"
        domain = random.choice(domains)
        return f"https://{domain}{path}"

    def company(self) -> str:
        """生成公司名"""
        companies = ["科技公司", "数据公司", "智能公司", "创新公司", "解决方案公司"]
        return random.choice(companies)

    def job(self) -> str:
        """生成职位"""
        jobs = ["软件工程师", "产品经理", "数据分析师", "系统架构师", "技术总监"]
        return random.choice(jobs)

    def phone_number(self) -> str:
        """生成电话号码"""
        self._counters["phone"] = self._counters.get("phone", 0) + 1
        return f"138{self._counters['phone']:08d}"

    def address(self) -> str:
        """生成地址"""
        cities = ["北京", "上海", "广州", "深圳", "杭州"]
        districts = ["朝阳区", "海淀区", "浦东新区", "南山区", "西湖区"]
        street_num = self._counters.get("address", 1)
        self._counters["address"] = street_num + 1

        city = random.choice(cities)
        district = random.choice(districts)
        return f"{city}{district}测试街道{street_num}号"

    def color(self) -> str:
        """生成颜色"""
        colors = ["红色", "蓝色", "绿色", "黄色", "紫色", "橙色", "黑色", "白色"]
        return random.choice(colors)

    def currency_code(self) -> str:
        """生成货币代码"""
        codes = ["CNY", "USD", "EUR", "JPY", "GBP"]
        return random.choice(codes)

    def country(self) -> str:
        """生成国家"""
        countries = [
            "中国",
            "美国",
            "日本",
            "德国",
            "英国",
            "法国",
            "加拿大",
            "澳大利亚",
        ]
        return random.choice(countries)

    def language_code(self) -> str:
        """生成语言代码"""
        codes = ["zh-CN", "en-US", "ja-JP", "de-DE", "fr-FR"]
        return random.choice(codes)

    def locale(self) -> str:
        """生成地区设置"""
        locales = ["zh_CN", "en_US", "ja_JP", "de_DE", "fr_FR"]
        return random.choice(locales)

    def timezone_str(self) -> str:
        """生成时区"""
        timezones = ["Asia/Shanghai", "America/New_York", "Europe/London", "Asia/Tokyo"]
        return random.choice(timezones)
