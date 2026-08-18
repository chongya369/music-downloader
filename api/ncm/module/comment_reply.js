const { resourceTypeMap } = require('../util/config.json')
// 发送评论

const createOption = require('../util/option.js')
module.exports = (query, request) => {
  const data = {
    threadId: resourceTypeMap[query.type] + query.id,
    commentId: query.cid,
    content: query.content,
    resourceType: '0',
  }
  return request(
    `/api/v1/resource/comments/reply`,
    data,
    createOption(query, 'xeapi', 'v3'),
  )
}
