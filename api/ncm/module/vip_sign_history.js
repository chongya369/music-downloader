// 黑胶乐签打卡历史 / 状态查询
// 支持传入 type=0（用户信息栏）或 type=1（黑胶乐签）

const createOption = require('../util/option.js')
module.exports = (query, request) => {
  const data = {
    type: query.type || '0',
  }
  return request(
    `/api/vipnewcenter/app/minidesk/music/sign/pc`,
    data,
    createOption(query, 'eapi'),
  )
}
