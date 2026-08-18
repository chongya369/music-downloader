// esbuild 构建产物启动入口
const path = require('path')

// 默认监听本机
let host = '127.0.0.1'
let port = '3000'

// 简单解析命令行参数
const args = process.argv.slice(2)
for (let i = 0; i < args.length; i++) {
  if (args[i] === '--host' && args[i + 1]) {
    host = args[i + 1]
  }
  if (args[i] === '--port' && args[i + 1]) {
    port = args[i + 1]
  }
}

// 设置环境变量供 app.js / server.js 读取
process.env.HOST = host
process.env.PORT = port

console.log('Starting NCM API on ' + host + ':' + port)

// 运行时 asset 根目录就是 dist/ 本身
// NODE_PATH 采用「追加」而非覆盖：允许 bridge.py（回退目录）注入的
// NODE_PATH 与本目录一起参与模块解析，否则回退场景 require('jsdom') 等
// 无法命中用户缓存区里安装的 node_modules。
const dirname = path.resolve(__dirname)
if (process.env.NODE_PATH) {
  process.env.NODE_PATH += path.delimiter + dirname
} else {
  process.env.NODE_PATH = dirname
}
require('./app.js')
