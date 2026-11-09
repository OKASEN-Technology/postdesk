"""
The maker (docs/roadmap.md, M1): facts in, a rendered post in the queue
out.

    Facts (a source) + a template (templates/*.yaml)
      -> plan: the slots, each a typed slide with a brief, the fields the
         model fills and the ones fixed from the facts
      -> write_copy: one structured Claude call for every slide's words,
         the caption, alt text and the Story; check_copy sends the problems
         back, twice at most
      -> build_slides: the render specs
      -> render: each spec to the renderer, a JPEG back, stored with its
         provenance; the spec kept on the post so one slide can be redone
      -> queue.create_post: the carousel (or image) and its Story, as drafts

Nothing here publishes. Every number on a slide is checked against the
facts, so the model cannot add one.
"""

from postdesk.maker.plans import Slot, Template, TemplateError, load_template, plan, template_dirs  # noqa: F401
from postdesk.maker.render import Renderer, RenderError, render_spec, validate_spec  # noqa: F401
from postdesk.maker.run import AGENT, build_slides, make, revise  # noqa: F401
from postdesk.maker.words import (  # noqa: F401
    FIELD_RULES,
    LIMITS,
    SLIDE_COPY,
    MakerError,
    check_copy,
    known_numbers,
    patch_spec,
    slide_words,
    system_prompt,
    write_copy,
)
