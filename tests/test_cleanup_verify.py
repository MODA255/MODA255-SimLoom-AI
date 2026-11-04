from orchestrator.cleanup_verify import cleanup_and_verify


def test_cleanup_verification_marks_orphans():
    class Adapter:
        def cleanup_resource(self, kind, resource_id): raise RuntimeError('cleanup failed')
    result=cleanup_and_verify(Adapter(), {'task_id':'t1'})
    assert result['cleanup_status']=='partial'
    assert result['verification']['remaining_resource_count']==1
    assert result['verification']['verified'] is False
