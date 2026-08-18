// 云小编每日签到

const createOption = require('../util/option.js')

module.exports = (query, request) => {
  return request(`/api/rep/ugc/user/sign`, {}, createOption(query, 'eapi'))
}
