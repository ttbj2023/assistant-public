"""Local Memory模块Factory集合

提供针对本地记忆系统中各个组件的专门数据工厂，支持生成标准化的测试数据。
"""

from __future__ import annotations

from datetime import datetime, timedelta

from src.storage.models.conversation import (
    ConversationData,
    ConversationIndex,
)
from tests.factories.base_factory import BaseFactory


class ConversationDataFactory(BaseFactory):
    """ConversationData专用工厂"""

    def __init__(self):
        super().__init__()

    def create(
        self, user_id: str = "test_user", thread_id: str = "test_thread", **kwargs
    ) -> ConversationData:
        """创建ConversationData实例"""
        faker = self.faker()
        sequence_id = self.get_sequence("conversation")

        return ConversationData(
            user_id=user_id,
            thread_id=thread_id,
            user_message=kwargs.get("user_message", faker.sentence()),
            assistant_response=kwargs.get("assistant_response", faker.sentence()),
            round_number=kwargs.get("round_number", sequence_id),
            timestamp=kwargs.get("timestamp", faker.date_time_this_year()),
            message_count=kwargs.get("message_count", faker.random_int(1, 5)),
            token_usage=kwargs.get("token_usage", faker.random_int(50, 500)),
            summary=kwargs.get("summary", faker.sentence()),
            topic=kwargs.get("topic", faker.word()),
        )

    def create_batch(
        self,
        count: int,
        user_id: str = "test_user",
        thread_id: str = "test_thread",
        **kwargs,
    ) -> list[ConversationData]:
        """创建批量ConversationData"""
        conversations = []
        base_time = datetime.now() - timedelta(hours=count)

        for i in range(count):
            conv_data = self.create(
                user_id=user_id,
                thread_id=thread_id,
                round_number=i + 1,
                timestamp=base_time + timedelta(minutes=i * 30),
                user_message=f"用户消息 {i + 1}",
                assistant_response=f"助手回复 {i + 1}",
                title=f"对话 {i + 1}",
                **kwargs,
            )
            conversations.append(conv_data)

        return conversations

    def create_with_special_content(self) -> ConversationData:
        """创建包含特殊内容的ConversationData"""
        special_contents = [
            ("中文测试内容 🚀 包含emoji", "中文回复内容 ✅ 测试成功"),
            ("English with العربية text", "English response with العربية text"),
            ("Special chars: !@#$%^&*()", "Response with special chars"),
            ("Mathematical symbols: ∑∏∫∆∇∂", "Mathematical response: αβγδεζηθ"),
        ]

        user_msg, assistant_msg = special_contents[
            self.get_sequence("special") % len(special_contents)
        ]

        return self.create(
            user_message=user_msg,
            assistant_response=assistant_msg,
            title="特殊内容对话",
            keywords="特殊字符,Unicode,测试",
        )

    def create_long_content(self, multiplier: int = 10) -> ConversationData:
        """创建长内容的ConversationData"""
        faker = self.faker()
        long_user_message = " ".join([faker.sentence() for _ in range(multiplier)])
        long_assistant_response = " ".join([
            faker.sentence() for _ in range(multiplier)
        ])

        return self.create(
            user_message=long_user_message,
            assistant_response=long_assistant_response,
            title="长对话测试",
            token_usage=len(long_user_message + long_assistant_response) * 2,
        )


class ConversationIndexFactory(BaseFactory):
    """ConversationIndex专用工厂"""

    def __init__(self):
        super().__init__()

    def create(
        self, user_id: str = "test_user", thread_id: str = "test_thread", **kwargs
    ) -> ConversationIndex:
        """创建ConversationIndex实例"""
        faker = self.faker()
        sequence_id = self.get_sequence("conversation_index")

        conversation = ConversationIndex(
            user_id=user_id,
            thread_id=thread_id,
            round_number=kwargs.get("round_number", sequence_id),
            user_message=kwargs.get("user_message", faker.sentence()),
            assistant_response=kwargs.get("assistant_response", faker.sentence()),
            summary=kwargs.get("summary", faker.sentence()),
            topic=kwargs.get("topic", faker.word()),
            message_count=kwargs.get("message_count", faker.random_int(1, 5)),
            token_usage=kwargs.get("token_usage", faker.random_int(50, 500)),
            created_at=kwargs.get("created_at", faker.date_time_this_year()),
            updated_at=kwargs.get("updated_at", faker.date_time_this_year()),
        )

        return conversation

    def create_batch(
        self,
        count: int,
        user_id: str = "test_user",
        thread_id: str = "test_thread",
        **kwargs,
    ) -> list[ConversationIndex]:
        """创建批量ConversationIndex"""
        conversations = []
        base_time = datetime.now() - timedelta(days=count)

        for i in range(count):
            conv_index = self.create(
                user_id=user_id,
                thread_id=thread_id,
                round_number=i + 1,
                created_at=base_time + timedelta(days=i),
                updated_at=base_time + timedelta(days=i),
                user_message=f"用户消息 {i + 1}",
                assistant_response=f"助手回复 {i + 1}",
                title=f"对话 {i + 1}",
                **kwargs,
            )
            conversations.append(conv_index)

        # 按时间倒序排列（最新的在前）
        conversations.sort(key=lambda x: x.created_at, reverse=True)
        return conversations

    def create_with_time_range(
        self,
        days: int = 7,
        conversations_per_day: int = 3,
        user_id: str = "test_user",
        thread_id: str = "test_thread",
        **kwargs,
    ) -> list[ConversationIndex]:
        """创建有时间范围的ConversationIndex"""
        conversations = []
        current_round = 1

        for day_offset in range(days):
            target_date = datetime.now() - timedelta(days=day_offset)

            for day_conv in range(conversations_per_day):
                conv_index = self.create(
                    user_id=user_id,
                    thread_id=thread_id,
                    round_number=current_round,
                    created_at=target_date,
                    updated_at=target_date,
                    user_message=f"{day_offset}天前的用户消息 {day_conv + 1}",
                    assistant_response=f"{day_offset}天前的助手回复 {day_conv + 1}",
                    title=f"{target_date.strftime('%Y-%m-%d')}的对话 {day_conv + 1}",
                    **kwargs,
                )
                conversations.append(conv_index)
                current_round += 1

        # 按时间倒序排列（最新的在前）
        conversations.sort(key=lambda x: x.created_at, reverse=True)
        return conversations
