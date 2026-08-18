// 云小编查询会员任务状态
//
// 状态 (data.status)
// 10: 用户积分达50，可免费领取1日黑胶会员
// 20: 用户积分已达50，可免费领取1日黑胶会员
// 30: 已领取1日黑胶会员，明天再来吧~

const createOption = require('../util/option.js')

module.exports = (query, request) => {
  return request(`/api/rep/ugc/user/vip`, {}, createOption(query, 'eapi'))
}
