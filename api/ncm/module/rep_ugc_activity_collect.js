// 云小编领取任务积分
//
// activityId:
// 调用 rep/ugc/activity/get 获取

const createOption = require('../util/option.js')

module.exports = (query, request) => {
  const data = {
    activityId: query.activityId || '5001',
  }
  return request(
    `/api/rep/ugc/activity/collect`,
    data,
    createOption(query, 'eapi'),
  )
}
