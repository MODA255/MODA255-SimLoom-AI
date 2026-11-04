from .live_source_search import LiveSourceSearchSkill, LiveSourceSearchInput


def search_live_candidates(skill, query, limit=10):
    result=skill.run(LiveSourceSearchInput(query=query,limit=limit,required=['live'],authorization_required=True))
    return [item.url for item in result.results], result.model_dump()
