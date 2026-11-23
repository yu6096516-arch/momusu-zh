魔物娘TD汉化 / Monmusu TD Chinese v0.1.0
Windows 10 / Windows 11 x64 本地测试包

==================== 首次使用 ====================
1. 将 ZIP 完整解压到可写目录，支持中文和空格路径。不要在 ZIP 内直接运行。
2. 完全退出对应浏览器，再双击“启动汉化.bat”。

   首次启动 mitmdump 会自动生成 data 目录及本机 CA 证书。
   data\mitmproxy-ca.pem 包含私钥，不要分享 data 目录。

   若未关闭浏览器就运行脚本，已运行的浏览器会忽略新的代理参数，导致汉化不生效；
   请自行退出浏览器（本工具不会自动关闭浏览器）。

3. 保持汉化窗口打开。服务关闭后，仍在用该代理的浏览器可能无法联网，
   重新启动浏览器即可恢复。


==================== 配置（config.ini） ====================
唯一玩家配置文件，改完保存后再运行“启动汉化.bat”。

listen_port   本机监听端口，默认 8080，范围 1–65535。被占用时自行修改。
upstream_port 留空直连；填写数字则使用 http://127.0.0.1:<该端口> 的现有代理。
              常见端口：clash 7890 / 岛风go 8099 / beptard 8444 / ACGP 8123 等。
upstream_insecure
              true（默认）：放宽“本机上游代理”这一段的证书校验。
              非clash加速器会替换 TLS 证书，严格校验会报
              502 Bad Gateway 或 Certificate verify failed。
              使用 clash / v2ray 这类不替换证书的普通 HTTP 代理时可改为 false。
type          chrome / edge / custom。找不到 Chrome 时可改为 edge。
path          仅 custom 使用，填写浏览器 EXE 的绝对路径，可带双引号。
args          留空时，所有浏览器统一使用：
              --proxy-server="http://127.0.0.1:<listen_port>" --disable-quic
              填写后将完整替代上面两项（不再附加默认参数），引号与空格原样传给浏览器。
              例：args = --proxy-server="http://127.0.0.1:9090" --disable-quic --start-maximized
              若覆盖了默认参数却没有写正确的 --proxy-server，汉化不会生效。

debug         false（默认）。改成 true 为开发者调试模式，显示完整网络日志。
              普通玩家保持 false，控制台只显示汉化器状态。

