from typing import Literal
from pydantic import BaseModel, ConfigDict, Field


class Strict(BaseModel):
    model_config = ConfigDict(extra='forbid')


class Assignment(Strict):
    worker: str
    task_id: str
    instructions: str = ''


class Rejection(Strict):
    submission_id: str
    rating: int = Field(ge=1, le=5)
    feedback: str = Field(min_length=1)


class ManagerEnd(Strict):
    scratchpad: str
    rejections: list[Rejection] = []
    assignments: list[Assignment] = []


class WorkerEnd(Strict):
    scratchpad: str


class ReadArgs(Strict):
    operation: Literal['list','read','search']
    path: str = '.'
    query: str = ''
    start: int = Field(default=1, ge=1)
    limit: int = Field(default=160, ge=1, le=300)


class PatchArgs(Strict):
    task_id: str
    diff: str = Field(min_length=1, max_length=100000)
    explanation: str = Field(min_length=1, max_length=12000)


class AcceptArgs(Strict):
    submission_id: str
    rating: int = Field(ge=1, le=5)
    feedback: str = Field(min_length=1, max_length=12000)


class MessageArgs(Strict):
    recipient: str
    text: str = Field(min_length=1, max_length=6000)


class PublicMessageArgs(Strict):
    text: str = Field(min_length=1, max_length=6000)


class StatementArgs(Strict):
    text: str = Field(min_length=1, max_length=6000)


class BallotArgs(Strict):
    candidate_id: str


class FuturePlanEnd(Strict):
    future_plan: str = Field(min_length=1, max_length=6000)


def definition(name, description, model):
    return dict(type='function', function=dict(name=name, description=description, parameters=model.model_json_schema()))


def tools(manager, private, public=False):
    result = [definition('read_search_files','List, read, or search approved application files. Paths are relative to the project root. Read results include line numbers; searches are literal.', ReadArgs)]
    if manager:
        result.append(definition('accept_patch','Accept a submitted patch with a 1–5 rating and public feedback, and mechanically apply it. Review submissions in their supplied order. No correctness tests are run.', AcceptArgs))
    else:
        result.append(definition('submit_patch','Submit one unified diff privately to the manager for your assigned task. Use a/ and b/ project-relative paths and real line numbers with context. This does not edit files.', PatchArgs))
        if public:
            result.append(definition('submit_public_message','Post a concise progress update, finding, or coordination message to the public board. Delivered after all workers finish, before manager review; workers see it next round. Do not post patches or private content. Sender and round are assigned by the controller.', PublicMessageArgs))
    if private:
        result.append(definition('submit_private_message','Send a private message to a specific other agent. During work, worker messages are buffered until every worker finishes; the manager sees them at review and workers next round. Manager messages arrive before the following work round.',MessageArgs))
    return result
