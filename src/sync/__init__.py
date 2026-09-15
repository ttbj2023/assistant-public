"""同步子系统: assistant 用户级数据 <-> Microsoft Graph (To Do / Outlook 日历).

模块划分:
- msgraph_client: MSA device code flow 授权 + Graph API 客户端
- token_store: 用户级 OAuth token 文件存储 (0600, 原子回写)

设计文档: docs/development/msgraph-sync-design.md
"""
