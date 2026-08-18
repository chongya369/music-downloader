// 云小编获取用户详情

const createOption = require('../util/option.js')

module.exports = (query, request) => {
  return request(`/api/rep/ugc/user/get`, {}, createOption(query, 'eapi'))
}
