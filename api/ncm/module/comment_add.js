const { resourceTypeMap } = require('../util/config.json')
// 发送评论

const createOption = require('../util/option.js')
module.exports = (query, request) => {
  const data = {
    threadId: resourceTypeMap[query.type] + query.id,
    content: query.content,
    resourceType: '0',
    expressionPicId: '-1',
    bubbleId: '-1',
  }
  return request(
    `/api/resource/comments/add`,
    data,
    createOption(query, 'xeapi', 'v3'),
  )
}
