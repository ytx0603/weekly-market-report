# 📡 每周趋势雷达

> 资本 × 代码 × 巨头的交叉信号  
> 每周日自动生成趋势分析报告，发送到你的邮箱。

## 工作原理

```
每周日 20:00（北京时间）
    │
    ▼
GitHub Actions 自动触发（云端，不需要你开机）
    │
    ├─→ 拉取美股 14 个板块 ETF 涨跌（yfinance）
    ├─→ 拉取 GitHub 本周最热 + 飙升项目（GitHub API）
    ├─→ 拉取财经新闻（Reuters / CNBC / Yahoo Finance RSS）
    ├─→ 拉取科技公司动态（SpaceX/Tesla/OpenAI/Apple/NVIDIA/Google...）
    │
    ▼
数据打包 → 喂给 DeepSeek LLM 分析
    │
    ├─→ 判断本周核心趋势信号
    ├─→ 各维度详细分析
    ├─→ 未来 2-4 周预判
    ├─→ 隐性关联分析（跨维度交叉验证）
    │
    ▼
生成 PDF + HTML 邮件 → 发送到你的 QQ 邮箱
```

## 快速开始（3 步）

### 第 1 步：准备密钥

| 密钥 | 怎么拿 |
|------|--------|
| **DeepSeek API Key** | 去 [platform.deepseek.com](https://platform.deepseek.com) 注册，点「API Keys」创建 |
| **QQ 邮箱授权码** | QQ邮箱 → 设置 → 账户 → POP3/SMTP服务 → 开启 → 获取授权码（**不是 QQ 密码！**） |

### 第 2 步：创建 GitHub 仓库并设置 Secrets

```bash
# 在 GitHub 上新建一个 **私有** 仓库（Private repository）

# 把代码推上去
cd C:\Users\Lenovo\Projects\weekly-market-report
git init
git add .
git commit -m "每周趋势雷达 v1"
git remote add origin https://github.com/你的用户名/weekly-market-report.git
git push -u origin main
```

然后去 GitHub 仓库页面：**Settings → Secrets and variables → Actions → New repository secret**，添加以下 4 个 secret：

| Secret 名 | 值 |
|-----------|-----|
| `DEEPSEEK_API_KEY` | `sk-xxxxxxxxxxxxxxxx` |
| `QQ_EMAIL` | `你的QQ号@qq.com` |
| `QQ_SMTP_CODE` | `QQ邮箱授权码（16位）` |
| `RECIPIENT_EMAIL` | `收报告的邮箱（一般跟上面一样）` |

### 第 3 步：测试

去 GitHub 仓库页面：**Actions → 每周趋势雷达 → Run workflow**，手动触发一次，等 2-3 分钟，检查邮箱。

## 本地测试（可选）

```bash
# 1. 安装依赖
pip install -r requirements.txt

# 2. 创建 .env 文件，填入真实密钥
cp .env.example .env
# 编辑 .env，填入你的密钥

# 3. 运行
python weekly_report.py
```

## 成本

- **GitHub Actions**：私有仓库每月 2000 分钟免费，每周跑一次用不到 20 分钟
- **DeepSeek API**：每次报告约消耗 5000-10000 tokens，约 ¥0.002-0.005，一个月不到 2 分钱
- **QQ 邮箱 SMTP**：免费，每日限额足够

## 安全

- ✅ 所有密钥存储在 GitHub Secrets，代码中无任何硬编码
- ✅ `.env` 文件在 `.gitignore` 中，不会被提交
- ✅ 建议使用**私有仓库**

---

⚠️ 本报告由 AI 自动生成，仅供参考，不构成任何投资建议。
