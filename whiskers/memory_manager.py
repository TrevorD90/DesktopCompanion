# memory_manager.py — Manages all persistent student data for Whiskers

import json
import os
from datetime import date

import config


def _default_memory():
    """Create a default memory structure from config values."""
    return {
        'student': {
            'name': config.STUDENT_NAME,
            'age': config.STUDENT_AGE,
            'grade': config.STUDENT_GRADE,
            'first_session': str(date.today()),
            'total_sessions': 0
        },
        'subjects': {
            'math': {
                'topics_covered': [],
                'current_topic': '',
                'strengths': [],
                'needs_practice': []
            },
            'science': {
                'topics_covered': [],
                'current_topic': '',
                'strengths': [],
                'needs_practice': []
            },
            'reading': {
                'topics_covered': [],
                'current_topic': '',
                'strengths': [],
                'needs_practice': []
            }
        },
        'recent_sessions': [],
        'personality': {
            'learning_style': '',
            'gets_frustrated_with': '',
            'responds_well_to': '',
            'favorite_topics': []
        }
    }


def load_memory():
    """Load student memory from JSON file. Creates default if missing."""
    # Ensure the data directory exists
    os.makedirs(os.path.dirname(config.MEMORY_FILE), exist_ok=True)

    if os.path.exists(config.MEMORY_FILE):
        with open(config.MEMORY_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    else:
        memory = _default_memory()
        save_memory(memory)
        return memory


def save_memory(memory_dict):
    """Save the memory dictionary to the JSON file."""
    os.makedirs(os.path.dirname(config.MEMORY_FILE), exist_ok=True)
    with open(config.MEMORY_FILE, 'w', encoding='utf-8') as f:
        json.dump(memory_dict, f, indent=2, ensure_ascii=False)


def get_context_summary():
    """Return a 3-5 sentence summary of the student for injection into the AI system prompt."""
    memory = load_memory()
    student = memory['student']
    subjects = memory['subjects']
    personality = memory['personality']
    recent = memory['recent_sessions']

    # Basic identity
    summary = (
        f"{student['name']} is a {student['age']}-year-old in {student['grade']}. "
        f"They have completed {student['total_sessions']} tutoring sessions so far."
    )

    # Current topics and strengths across all subjects
    current_topics = []
    strengths = []
    needs_practice = []
    for subj_name, subj_data in subjects.items():
        if subj_data.get('current_topic'):
            current_topics.append(f"{subj_data['current_topic']} in {subj_name}")
        strengths.extend(subj_data.get('strengths', []))
        needs_practice.extend(subj_data.get('needs_practice', []))

    if current_topics:
        summary += f" Currently working on: {', '.join(current_topics)}."
    if strengths:
        summary += f" Strengths include: {', '.join(strengths)}."
    if needs_practice:
        summary += f" Needs more practice with: {', '.join(needs_practice)}."

    # Personality and learning style
    if personality.get('learning_style'):
        summary += f" {student['name']} is a {personality['learning_style']} learner"
        if personality.get('responds_well_to'):
            summary += f" who responds well to {personality['responds_well_to']}"
        summary += "."
    if personality.get('gets_frustrated_with'):
        summary += f" They can get frustrated with {personality['gets_frustrated_with']}."
    if personality.get('favorite_topics'):
        summary += f" They love {', '.join(personality['favorite_topics'])}."

    # Most recent session
    if recent:
        last = recent[-1]
        summary += (
            f" In the last session ({last['date']}), they worked on {last['topic']} "
            f"in {last['subject']}"
        )
        if last.get('breakthrough'):
            summary += f" and {last['breakthrough'].lower()}"
        summary += "."
        if last.get('still_working_on'):
            summary += f" Still working on: {last['still_working_on']}."

    return summary


def log_session(subject, topic, breakthrough, notes):
    """Append a session record. Keeps the last 10 sessions."""
    memory = load_memory()

    session_entry = {
        'date': str(date.today()),
        'subject': subject,
        'topic': topic,
        'breakthrough': breakthrough,
        'still_working_on': notes
    }

    memory['recent_sessions'].append(session_entry)
    # Keep only the last 10 sessions
    memory['recent_sessions'] = memory['recent_sessions'][-10:]
    # Increment total session count
    memory['student']['total_sessions'] += 1

    save_memory(memory)


def update_topic_progress(subject, topic, mastered=False):
    """Update topic tracking for a given subject.

    If mastered: move topic to topics_covered and strengths, remove from needs_practice.
    If not mastered: set as current_topic, add to needs_practice if not already there.
    """
    memory = load_memory()

    # Create subject entry if it doesn't exist
    if subject not in memory['subjects']:
        memory['subjects'][subject] = {
            'topics_covered': [],
            'current_topic': '',
            'strengths': [],
            'needs_practice': []
        }

    subj = memory['subjects'][subject]

    if mastered:
        # Add to topics_covered if not already there
        if topic not in subj['topics_covered']:
            subj['topics_covered'].append(topic)
        # Add to strengths if not already there
        if topic not in subj['strengths']:
            subj['strengths'].append(topic)
        # Remove from needs_practice
        if topic in subj['needs_practice']:
            subj['needs_practice'].remove(topic)
        # Clear current_topic if it was this topic
        if subj['current_topic'] == topic:
            subj['current_topic'] = ''
    else:
        # Set as current topic
        subj['current_topic'] = topic
        # Add to needs_practice if not already there
        if topic not in subj['needs_practice']:
            subj['needs_practice'].append(topic)

    save_memory(memory)
