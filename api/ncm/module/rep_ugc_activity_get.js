// 云小编活动信息

const createOption = require('../util/option.js')

module.exports = (query, request) => {
  return request(`/api/rep/ugc/activity/get`, {}, createOption(query, 'eapi'))
}
