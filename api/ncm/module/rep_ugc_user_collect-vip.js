// 云小编领取一日会员
//
// 注：前提条件见 rep/ugc/user/vip

const createOption = require('../util/option.js')

module.exports = (query, request) => {
  const data = {
    activityId: query.activityId || '5001',
  }
  return request(
    `/api/rep/ugc/user/collect-vip`,
    data,
    createOption(query, 'eapi'),
  )
}
