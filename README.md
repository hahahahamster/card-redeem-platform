# 卡密提取平台

一个轻量的前后端分离卡密系统：

- 前台：Vue3 + Vite，只有卡密输入框和提取按钮。
- 后台：创建商品、手动上货、批量生成卡密、查看库存和卡密状态。
- 后端：Python 标准库 HTTP 服务 + SQLite，本地数据库文件在 `server/data/app.db`。

## 服务器快速部署

在 Linux 服务器上执行：

```bash
git clone https://github.com/hahahahamster/card-redeem-platform.git
cd card-redeem-platform
sudo DOMAIN=your-domain.com bash deploy/install.sh
```

如果你想接收证书过期/续期失败提醒，可以加邮箱：

```bash
sudo DOMAIN=your-domain.com EMAIL=your-email@example.com bash deploy/install.sh
```

如果你暂时只想跑 HTTP，不申请 HTTPS：

```bash
sudo DOMAIN=your-domain.com ENABLE_SSL=0 bash deploy/install.sh
```

部署完成后访问：

```text
https://your-domain.com
```

后台地址：

```text
https://your-domain.com/admin
```

这个部署不需要 Docker。脚本会安装并配置 `nginx`、`systemd` 服务、`certbot` HTTPS 证书。邮箱不是必填，但建议填写，方便接收证书提醒。

请把示例里的 `your-domain.com` 换成你自己的域名；公开仓库不要直接写真实业务域名。

## 目录

```text
D:\card
├─ client          # Vue3 前端
├─ server          # Python + SQLite 后端
└─ README.md
```

## 推荐启动方式

```powershell
cd D:\card\server
python app.py
```

现在后端会同时提供接口和页面，不需要再单独运行 `npm run dev`。启动后访问：

```text
前台：http://127.0.0.1:8787
后台：http://127.0.0.1:8787/admin
```

默认后台密码：

```text
admin123456
```

正式使用前建议改密码：

```powershell
$env:CARD_ADMIN_PASSWORD="你的强密码"
python app.py
```

## 前端开发模式

只有在你修改 Vue 页面并需要重新构建时，才需要 Node.js：

```powershell
cd D:\card\client
npm install
npm run dev
```

开发模式访问：

```text
前台：http://127.0.0.1:5173
后台：http://127.0.0.1:5173/admin
```

## Linux 一键部署

把项目上传或克隆到服务器后，在项目根目录执行：

```bash
sudo bash deploy/install.sh
```

默认会：

- 安装 `python3`、`nodejs`、`npm`、`nginx`
- 构建 Vue 前端
- 把项目部署到 `/opt/card-redeem`
- 创建并启动 `card-redeem` systemd 服务
- 配置 Nginx 反代到 `127.0.0.1:8787`

如果有域名并希望自动申请 HTTPS 证书：

```bash
sudo DOMAIN=your-domain.com bash deploy/install.sh
```

邮箱可选：

```bash
sudo DOMAIN=your-domain.com EMAIL=your-email@example.com bash deploy/install.sh
```

如果一个证书绑定多个域名，用英文逗号分隔：

```bash
sudo DOMAIN=example.com,www.example.com,shop.example.com bash deploy/install.sh
```

不需要 Docker。脚本会使用：

- `python3` 运行后端
- `SQLite` 保存数据
- `systemd` 保持服务常驻
- `nginx` 反向代理
- `certbot` 自动申请和续期 HTTPS 证书

Cloudflare DNS 建议：

- `your-domain.com` 的 A 记录指向服务器公网 IP。
- 申请证书前确保服务器开放 80 和 443 端口。
- 如果 Cloudflare 开启代理后证书申请失败，先把小云朵改成 DNS only，证书成功后再开启代理。
- Cloudflare SSL/TLS 模式建议使用 `Full` 或 `Full (strict)`，不要用 `Flexible`。

常用命令：

```bash
sudo systemctl status card-redeem
sudo systemctl restart card-redeem
sudo journalctl -u card-redeem -f
```

## 使用流程

1. 进入后台 `/admin`，用后台密码登录。
2. 创建一个电子商品。
3. 在“手动上货”里选择商品，一行写一条发货内容，比如下载链接、账号密码或文本；也可以上传文件库存。
4. 在“生成卡密”里选择商品并生成卡密。
5. “每张发货数量”表示用户兑换一张卡后发出多少条该商品库存。例如填 `100`，一张卡会一次发出 100 条库存。
6. 用户到前台输入卡密，系统会发出对应数量的库存内容，并把卡密标记为已使用。

## 后台管理

- 已兑换商品可以“恢复”回未发库存，也可以“删除”记录。
- 单个商品管理里可以勾选多条未发或已兑换商品，并批量删除。
- 已使用卡密可以整张“恢复”，恢复后这张卡密会变回未使用，发出的库存也会回到未发库存。
- 已使用或未使用卡密都可以删除；删除已使用卡密时，它关联的已发商品记录会一起删除。
- 用户提取成功后，文本商品可以直接复制或下载 TXT；文件商品会显示原文件下载按钮。
- 后台右上角“设置”里可以修改后台登录密码，修改后下次登录使用新密码。
- 后台右上角“设置”里可以检测 GitHub 新版本；Linux 一键部署环境支持一键更新并自动重启服务。

## 后台一键更新

使用 `deploy/install.sh` 部署后，后台会显示“系统更新”模块。

```text
后台 -> 设置 -> 检测更新 -> 一键更新
```

更新会保留 `server/data` 里的数据库和后台配置，只替换程序代码、重新构建前端并重启 `card-redeem` 服务。

## 注意

SQLite 不需要单独安装数据库服务，比 JSON 文件更适合卡密这种需要防止重复兑换的场景。当前项目适合个人、小规模发卡使用；如果要公开部署，建议再加 HTTPS、反向代理、后台强密码和服务器防火墙。
