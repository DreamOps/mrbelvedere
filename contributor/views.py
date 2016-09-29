import json
import datetime
from django.http import HttpResponse
from django.http import HttpResponseRedirect
from django.shortcuts import get_object_or_404
from django.shortcuts import render
from django.template import RequestContext
from django.contrib.auth.decorators import login_required
from contributor.forms import CreateContributionForm
from contributor.forms import ContributionEditBranchForm
from contributor.forms import ContributionCommitForm
from contributor.forms import ContributionSubmitForm
from contributor.models import Contributor
from contributor.models import Contribution
from contributor.models import ContributionSync

def contributor_home(request):
    if not request.user.is_anonymous():
        return HttpResponseRedirect('/contributor/%s' % request.user.username)

    return render(request, 'contributor/contributor_home.html')

@login_required
def contributor_contributions(request, username):
    try:
        contributor = Contributor.objects.get(user__username = username)
    except Contributor.DoesNotExist:
        contributor = Contributor(user = request.user)
        contributor.save()

    if contributor.user != request.user:
        return HttpResponse('Unauthorized', status=401)

    context = {
        'contributions': contributor.contributions.all().order_by('-id'),
    }

    return render(request, 'contributor/my_contributions.html', context)

def create_contribution(request):
    if request.user.is_anonymous():
        return HttpResponseRedirect('/login/github/?redirect=/contributor/create')

    # If the user doesn't have a github social auth, redirect to login
    if not request.user.social_auth.filter(provider='github').count():
        return HttpResponseRedirect('/login/github/?redirect=/contributor/create')
    
    try:
        contributor = Contributor.objects.get(user = request.user)
    except Contributor.DoesNotExist:
        contributor = Contributor(user = request.user)
        contributor.save()
       
    if request.method == 'POST':
        form = CreateContributionForm(request.POST)
    
        if form.is_valid():
            form.save()
            return HttpResponseRedirect('/contributor/contributions/%s' % form.instance.id)

    else:
        initial = {'contributor': contributor.id}
        form = CreateContributionForm(initial=initial)
       
    return render(request, 'contributor/create_contribution.html', {'form': form})

@login_required
def contribution(request, contribution_id):
    contribution = get_object_or_404(Contribution, id = contribution_id)
   
    if not contribution.can_view(request.user): 
        if request.user.is_anonymous():
            return HttpResponseRedirect('/login/github/?redirect=/contributor/contributions/%s' % contribution_id)
        return HttpResponse('Unauthorized', status=401)

    # If no fork_branch is set, send to the edit branch form
    if not contribution.fork_branch:
        return HttpResponseRedirect('/contributor/contributions/%s/edit_branch' % contribution.id)

    # If no Salesforce org is linked, send to the edit salesforce org form
    if not contribution.sf_oauth:
        return HttpResponseRedirect('/contributor/contributions/%s/edit_salesforce_org' % contribution.id)

    last_sync = None
    res = list(contribution.syncs.all().order_by('-date_started')[:1])
    if res:
        last_sync = res[0]

    context = {
        'contribution': contribution,
        'syncs': contribution.syncs.all().order_by('-date_started'),
        'last_sync': last_sync,
    }

    return render(request, 'contributor/contribution.html', context)

@login_required
def contribution_edit_branch(request, contribution_id):
    contribution = get_object_or_404(Contribution, id = contribution_id)
    
    if not contribution.can_view(request.user): 
        return HttpResponse('Unauthorized', status=401)

    if request.method == 'POST':
        form = ContributionEditBranchForm(request.POST, instance=contribution)
        
        if form.is_valid():
            form.save()
            return HttpResponseRedirect('/contributor/contributions/%s' % contribution.id)

    else:
        form = ContributionEditBranchForm(instance=contribution)

    context = {'form': form, 'contribution': contribution}
    return render(request, 'contributor/contribution_edit_branch.html', context)

@login_required
def contribution_edit_salesforce_org(request, contribution_id):
    contribution = get_object_or_404(Contribution, id = contribution_id)
    
    if not contribution.can_view(request.user): 
        return HttpResponse('Unauthorized', status=401)

    # Set a redirect url in the session to be redirected back after a normal mpinstaller login
    redirect = '/contributor/contributions/%s/capture_salesforce_org' % contribution.id

    return render(request, 'contributor/contribution_edit_salesforce_org.html', {'contribution': contribution, 'redirect': redirect})

@login_required
def contribution_capture_salesforce_org(request, contribution_id):

    contribution = get_object_or_404(Contribution, id = contribution_id)
    
    if not contribution.can_view(request.user): 
        return HttpResponse('Unauthorized', status=401)

    # If there is no oauth key in session, redirect to the edit_salesforce_org view
    if 'oauth' not in request.session:
        return HttpResponseRedirect('/contributor/contributions/%s/edit_salesforce_org' % contribution.id)

    # Save the oauth dict as a json string on the Contribution
    contribution.sf_oauth = json.dumps(request.session['oauth'])
    contribution.save()

    # Clear the session for use by the installer
    del request.session['oauth']

    return HttpResponseRedirect('/contributor/contributions/%s' % contribution.id)

@login_required
def contribution_commit(request, contribution_id):
    contribution = get_object_or_404(Contribution, id = contribution_id)

    if not contribution.can_view(request.user): 
        return HttpResponse('Unauthorized', status=401)

    if request.method == 'POST':
        form = ContributionCommitForm(request.POST)
        if form.is_valid():
            # Create a ContributionSync to trigger background commit_from_org
            sync = ContributionSync(
                contribution = contribution,
                message = form.cleaned_data['message'],
            )
            sync.save()
            return HttpResponseRedirect('/contributor/contributions/%s' % contribution.id)
    else:
        form = ContributionCommitForm()

    return render(request, 'contributor/contribution_commit.html', {'contribution': contribution, 'form': form})

@login_required
def contribution_submit(request, contribution_id):
    contribution = get_object_or_404(Contribution, id = contribution_id)

    if not contribution.can_view(request.user): 
        return HttpResponse('Unauthorized', status=401)

    if request.method == 'POST':
        form = ContributionSubmitForm(request.POST)
        if form.is_valid():
            body = []
            if form.cleaned_data['reviewer_notes']:
                body.append(form.cleaned_data['reviewer_notes'])

            body.append('\n# Contribution Info\n')
            body.append('\nThis external contribution was submitted by @%s and Fixes #%s\n' % (contribution.contributor.user.username, contribution.github_issue))

            body.append('\n# Critical Changes\n')
            if form.cleaned_data['critical_changes']:
                body.append(form.cleaned_data['critical_changes'])
           
            body.append('\n# Changes\n')
            if form.cleaned_data['changes']:
                body.append(form.cleaned_data['changes'])

            body.append('\n# Issues\n')
            if form.cleaned_data['critical_changes']:
                body.append('Fixes #%s' % contribution.github_issue)

            data = {
                'title': contribution.title,
                'head': '%s:%s' % (contribution.contributor.user.username, contribution.fork_branch),
                'base': '%s' % contribution.get_default_branch()['ref'].replace('refs/heads/',''),
                'body': '\n'.join(body),
            }

            pull_request = contribution.github_api('/pulls', data)
            contribution.fork_pull = pull_request['number']
            contribution.save()

            return HttpResponseRedirect('/contributor/contributions/%s' % contribution.id)
    else:
        form = ContributionSubmitForm()

    return render(request, 'contributor/contribution_submit.html', {'contribution': contribution, 'form': form})

@login_required
def contribution_sync_state(request, contribution_id):
    contribution = get_object_or_404(Contribution, id = contribution_id)

    if not contribution.can_view(request.user): 
        return HttpResponse('Unauthorized', status=401)

    state = {
        'behind_main': contribution.state_behind_main,
        'undeployed_commit': contribution.state_undeployed_commit,
        'uncommitted_changes': contribution.state_uncommitted_changes,
    }
        
    return HttpResponse(json.dumps(state), content_type='application/json')

@login_required
def contribution_syncs(request, contribution_id):
    contribution = get_object_or_404(Contribution, id = contribution_id)

    if not contribution.can_view(request.user): 
        return HttpResponse('Unauthorized', status=401)

    syncs = contribution.syncs.all().order_by('-date_started')

    return render(request, 'contributor/contribution_syncs.html', {'contribution': contribution, 'syncs': syncs})

@login_required
def contribution_status(request, contribution_id):
    contribution = get_object_or_404(Contribution, id = contribution_id)

    if not contribution.can_view(request.user): 
        return HttpResponse('Unauthorized', status=401)

    status = {
        'id': contribution.id,
        'contributor': {
            'user': {
                'id': contribution.contributor.user.id,
                'username': contribution.contributor.user.username,
            },
        },
        'repo_name': contribution.get_main_repo_name(),
        'fork_pull': contribution.fork_pull,
        'state_behind_main': contribution.state_behind_main,
        'state_undeployed_commit': contribution.state_undeployed_commit,
        'state_uncommitted_changes': contribution.state_uncommitted_changes,
        'has_user_commit': contribution.has_user_commit(),
        'syncs': [],
        'current_sync': {},
    }

    # Include previous contribution syncs
    for sync in contribution.syncs.all().order_by('-date_started'):
        sync_data = {}
        sync_data['status'] = sync.status
        sync_data['message'] = sync.message
        sync_data['commit'] = sync.new_commit
        sync_data['type'] = sync.get_sync_type()
        sync_data['date_started'] = sync.date_started
        sync_data['pre_state_uncommitted_changes'] = sync.pre_state_uncommitted_changes
        sync_data['pre_state_deployed_commit'] = sync.pre_state_undeployed_commit
        sync_data['post_state_uncommitted_changes'] = sync.pre_state_uncommitted_changes
        sync_data['post_state_deployed_commit'] = sync.pre_state_undeployed_commit

        # If the sync failed, include the log
        if sync.status in ['pending','in_progress','failed']:
            sync_data['log'] = sync.log.replace('\n','<br />\n')
        else:
            sync_data['log'] = None

        # If there was a new installation, include information about the installation
        if sync.new_installation:
            sync_data['installation'] = {}
            sync_data['installation']['id'] = sync.new_installation.id
            sync_data['installation']['status'] = sync.new_installation.status
            
            if not status['syncs']:
                sync_data['installation']['log'] = sync.new_installation.log
                sync_data['installation']['steps'] = []
                for step in sync.new_installation.steps.all().order_by('-created'):
                    sync_data['installation']['steps'].append({
                        'id': step.id,
                        'status': step.status,
                        'version': unicode(step.version),
                        'log': step.log,
                    })

        status['syncs'].append(sync_data)

    if status['syncs'] and status['syncs'][0]['status'] in ['pending','in_progress']:
        status['current_sync'] = status['syncs'][0]

    dthandler = lambda obj: (
        obj.isoformat()
        if isinstance(obj, datetime.datetime)
        or isinstance(obj, datetime.date)
        else None)

    return HttpResponse(json.dumps(status, default=dthandler), content_type='application/json')
        
@login_required
def contribution_check_state(request, contribution_id):
    contribution = get_object_or_404(Contribution, id = contribution_id)

    if not contribution.can_view(request.user): 
        return HttpResponse('Unauthorized', status=401)

    contribution.save()

    return HttpResponseRedirect('/contributor/contributions/%s' % contribution.id)
