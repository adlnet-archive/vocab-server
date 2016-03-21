import logging

from django.contrib.auth import logout, login, authenticate
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User

from django.core.urlresolvers import reverse

from django.db import transaction
from django.db.models import Q

from django.forms import formset_factory
from django.http import HttpResponseRedirect, HttpResponseForbidden
from django.shortcuts import render

from django.views.decorators.csrf import csrf_protect
from django.views.decorators.http import require_http_methods

from .forms import RegisterForm, IRIForm, SearchForm, RequiredFormSet, VocabularyForm
from .models import RegisteredIRI, UserProfile, Vocabulary
from .tasks import notify_user

logger = logging.getLogger(__name__)

@require_http_methods(["GET"])
def home(request):
    return render(request, 'home.html')

@csrf_protect
@login_required
@require_http_methods(["GET", "POST"])
@transaction.atomic
def createIRI(request):
    IRIFormset = formset_factory(IRIForm, formset=RequiredFormSet)
    # if this is a POST request we need to process the form data
    if request.method == 'POST':
        # create a form instance and populate it with data from the request:
        formset = IRIFormset(request.POST)
        # check whether it's valid:
        if formset.is_valid():
            # process the data in form.cleaned_data as required
            for form in formset:
                if form.is_valid():
                    vocabulary = form.cleaned_data['vocabulary']
                    termType = form.cleaned_data['termType']
                    term = form.cleaned_data['term']
                    profile = UserProfile.objects.get(user=request.user)
                    iriobj = RegisteredIRI.objects.create(vocab=vocabulary, term_type=termType, term=term, userprofile=profile)
            return HttpResponseRedirect(reverse('iriCreationResults'))
            # redirect to a new URL:
    # if a GET (or any other method) we'll create a blank form
    else:
        formset = IRIFormset()
    return render(request, 'createIRI.html', {'formset': formset})

@csrf_protect
@require_http_methods(["POST", "GET"])
@transaction.atomic
def createUser(request):
    if request.method == 'GET':
        form = RegisterForm()
        return render(request, 'createUser.html', {"form": form})
    elif request.method == 'POST':
        form = RegisterForm(request.POST)
        if form.is_valid():
            name = form.cleaned_data['username']
            pword = form.cleaned_data['password']
            email = form.cleaned_data['email']
            # If username doesn't already exist
            if not User.objects.filter(username__exact=name).count():
                # if email doesn't already exist
                if not User.objects.filter(email__exact=email).count():
                    User.objects.create_user(name, email, pword)
                else:
                    return render(request, 'createUser.html', {"form": form, "error_message": "Email %s is already registered." % email})
            else:
                return render(request, 'createUser.html', {"form": form, "error_message": "User %s already exists." % name})
            # If a user is already logged in, log them out
            if request.user.is_authenticated():
                logout(request)
            new_user = authenticate(username=name, password=pword)
            login(request, new_user)
            return HttpResponseRedirect(reverse('home'))
        else:
            return render(request, 'createUser.html', {"form": form})

@csrf_protect
@login_required
@require_http_methods(["GET", "POST"])
@transaction.atomic
def createVocab(request):
    # if this is a POST request we need to process the form data
    if request.method == 'POST':
        # create a form instance and populate it with data from the request:
        form = VocabularyForm(request.POST)
        # check whether it's valid:
        if form.is_valid():
            # process the data in form.cleaned_data as required
            vocabName = form.cleaned_data['vocabName']
            vocabIRI = form.cleaned_data['vocabIRI']
            print "hi andy", vocabIRI, vocabName
            vocabobj = Vocabulary.objects.create(name=vocabName, iri=vocabIRI, user=request.user)
            # redirect to a new URL:
            return HttpResponseRedirect(reverse('rdfaResults'), {'vocabid': vocabobj.pk})

    # if a GET (or any other method) we'll create a blank form
    # starting at the vocab portion of the iri
    else:
        form = VocabularyForm()

    return render(request, 'rdfaForm.html', {'form': form})

@login_required
@require_http_methods(["GET"])
def userProfile(request):
    return render(request, 'userProfile.html')

@csrf_protect
@require_http_methods(["GET", "POST"])
def searchResults(request):
    if request.method == 'POST':
        query = None
        iris = None
        form = SearchForm(request.POST)
        if form.is_valid:
            if form.data['vocabulary']:
                query = Q(vocabulary__contains=form.data['vocabulary'])
            if form.data['term_type']:
                if query:
                    query = query | Q(term_type__contains=form.data['term_type'])
                else:
                    query = Q(term_type__contains=form.data['term_type'])
            if form.data['term']:
                if query:
                    query = query | Q(term__contains=form.data['term'])
                else:
                    query = Q(term__contains=form.data['term'])
            if query:
                iris = RegisteredIRI.objects.filter(query & Q(accepted=True))
        return render(request, 'searchResults.html', {"form":form, "iris":iris})
    else:
        form = SearchForm()
    return render(request, 'searchResults.html', {"form":form})

@login_required()
@require_http_methods(["GET"])
def iriCreationResults(request):
    results = RegisteredIRI.objects.filter(userprofile__user = request.user)
    return render(request, 'iriCreationResults.html', {'iris':results})

@login_required()
@require_http_methods(["GET", "POST"])
@transaction.atomic
def adminIRIs(request):
    if request.user.is_superuser:
        iris = RegisteredIRI.objects.filter(accepted=False, reviewed=False)
        if request.method == "GET":
            return render(request, 'adminIRIs.html', {"iris": iris})
        else:
            vocabulary = request.POST['hidden-vocabulary']
            term_type = request.POST['hidden-term_type']
            term = request.POST['hidden-term']
            try:
                iri = RegisteredIRI.objects.get(vocab=vocabulary, term_type=term_type, term=term)
            except RegisteredIRI.DoesNotExist as dne:
                logger.exception(dne.message)
            else:
                if request.POST['action'] == "Accept":
                    iri.accepted = True
                    iri.reviewed = True
                else:
                    iri.reviewed = True
                iri.save()
                notify_user.delay(iri.return_address(), iri.userprofile.user.email, iri.accepted)
        return render(request, 'adminIRIs.html', {"iris": iris})
    else:
        return HttpResponseForbidden()

@login_required()
@require_http_methods(["GET"])
def logout_view(request):
    logout(request)
    return HttpResponseRedirect(reverse('home'))

@csrf_protect
@login_required()
@require_http_methods(["GET"])
def rdfaForm(request):
    return render(request, 'rdfaForm.html')

@csrf_protect
@login_required()
@require_http_methods(["GET"])
def rdfaResults(request):
    # results = RegisteredIRI.objects.filter()
    return render(request, 'rdfaResults.html')
