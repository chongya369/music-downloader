// 云小编抽奖剩余次数查询
//
// activityId:
// 默认 6501202

const createOption = require('../util/option.js')

module.exports = (query, request) => {
  const data = {
    activityId: query.activityId || '6501202',
  }
  return request(
    `/api/middle/play/lottery/remain/chance`,
    data,
    createOption(query, 'eapi'),
  )
}
