'''
RunPod | API | Queries | User

Query for user information.
'''

QUERY_USER = """
query myself {
    myself {
        id
        pubKey
    }
}
"""
