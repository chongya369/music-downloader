// 一键领取所有会员成长值

const createOption = require('../util/option.js')
module.exports = (query, request) => {
  const data = {}
  return request(
    `/api/vipnewcenter/app/level/task/reward/getall`,
    data,
    createOption(query, 'xeapi'),
  )
}
