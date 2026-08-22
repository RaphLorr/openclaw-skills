---
name: reminder
description: 设置微信定时提醒。触发时使用 wechat_remind 工具。
---

# Reminder

当用户要求设置提醒、定时通知、重复消息时，必须使用 `wechat_remind` 工具。

不要直接调用 `cron` 工具创建提醒。不要只是文字回复"记下来了"——没有调用 `wechat_remind` 就没有提醒。

权限由 `openclaw.json` 中 `channels.wechat.reminder` 配置控制，工具内部自动检查。
