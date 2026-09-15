# fluidLabAgent

流体实验室智能体的第一版可运行系统。覆盖浏览器工作台、FastAPI、LangGraph、任务与记忆存储、设备网关、桌面 RPA bridge 和可选专业 Agent。

## 当前功能

- 浏览器创建和查看三类任务：翼板造风场、PIV 拍摄、流动控制。
- LangGraph 按 `检查 -> 规划 -> 派发 -> 等待 -> 评价` 推进任务。
- SQLite 保存本机数据；部署时可把 `LAB_DATABASE_URL` 换成 PostgreSQL。
- 模拟模式不连接设备，也能跑完整流程并显示时间序列。
- bridge 模式让设备电脑或 DaVis 电脑主动领取任务，再回报结果。
- 可选专业 Agent 只提供实验建议，不直接控制设备。

真实 Arduino 和影刀流程需要实验室的具体协议、端口、影刀外部调用方式和 DaVis 模板，当前没有进行真机联调。

## 5 分钟运行

需要 Python 3.11+ 和 [uv](https://docs.astral.sh/uv/)。

```bash
cd /Users/dengxiaolong/Research/ResearchProject/AIAgent/labAgentCode
uv sync --extra dev
uv run uvicorn labagent.api:app --reload
```

浏览器打开 <http://127.0.0.1:8000>。首次使用选择“本机模拟”，然后提交任务。

运行测试：

```bash
uv run pytest -q
```

## 外部设备模式

先在页面把执行模式改成“等待外部网关”。API 服务继续运行，再开一个终端执行：

```bash
# 用模拟 bridge 检查通信链路
uv run labagent-bridge --kind device --adapter simulation

# Arduino 串口示例，端口需要换成真实值
uv run labagent-bridge --kind device --adapter serial --port /dev/cu.usbmodemXXXX

# DaVis/影刀桥接示例，endpoint 是实验室自己实现或确认的适配服务
uv run labagent-bridge --kind desktop --adapter rpa --endpoint http://127.0.0.1:9001/run
```

复制 `.env.example` 中需要的变量到终端环境或 `.env`。服务暴露到其他电脑前必须设置 `LAB_API_TOKEN`。启用模型建议还需要 `LAB_USE_MODEL=1`、`LAB_MODEL` 和 `OPENAI_API_KEY`。

更详细的原理和操作说明见 [小白使用与原理说明.md](docs/小白使用与原理说明.md)。
